import SwiftUI

/// #333 — native first-run onboarding. Before this, config bootstrap was
/// CLI-only (`capdep init` / `capdep-setup`), so a GUI-only new user still
/// needed a terminal. This wizard drives the same daemon-owned steps
/// (`setupPlan` / `setupChecks` / setup actions) the CLI does, so a new user
/// reaches a working chat turn without leaving CapDepMac.
struct OnboardingWizardView: View {
    @EnvironmentObject private var model: CapDepAppModel
    @Environment(\.openWindow) private var openWindow
    @State private var step: Step = .welcome

    enum Step: Int, CaseIterable {
        case welcome
        case daemon
        case setup
        case connect
        case ready

        var title: String {
            switch self {
            case .welcome: return "Welcome"
            case .daemon: return "Daemon"
            case .setup: return "Setup"
            case .connect: return "Connect"
            case .ready: return "Ready"
            }
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            header
            stepIndicator
            Divider()
            ScrollView {
                stepContent
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.vertical, 4)
            }
            Divider()
            footer
        }
        .padding(28)
        .frame(minWidth: 620, minHeight: 560)
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Set up CapDep")
                .font(.title.weight(.semibold))
            Text("A few steps to a working assistant — no terminal required.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
        }
    }

    private var stepIndicator: some View {
        HStack(spacing: 8) {
            ForEach(Step.allCases, id: \.rawValue) { item in
                Capsule()
                    .fill(item.rawValue <= step.rawValue ? Color.accentColor : Color.secondary.opacity(0.25))
                    .frame(height: 5)
            }
        }
    }

    @ViewBuilder
    private var stepContent: some View {
        switch step {
        case .welcome: welcomeStep
        case .daemon: daemonStep
        case .setup: setupStep
        case .connect: connectStep
        case .ready: readyStep
        }
    }

    private var welcomeStep: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("What CapDep does")
                .font(.headline)
            Text("CapDep runs a local, security-first agent. Every action it takes flows through one policy chokepoint; sensitive or outbound steps pause for your approval. This wizard starts the daemon, applies a safe default surface, and (optionally) connects an account.")
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var daemonStep: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Background daemon")
                .font(.headline)
            OnboardingStatusRow(
                title: model.daemonConnection.statusTitle,
                detail: model.daemonConnection.detail,
                ok: model.connected,
            )
            if !model.connected {
                Button("Start / retry daemon") {
                    Task { await model.ensureDaemonRunning(); await model.refresh() }
                }
                .buttonStyle(.borderedProminent)
            } else {
                Text("The daemon is running. It owns policy, approvals, audit, and your sessions.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private var setupStep: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Readiness")
                .font(.headline)
            if model.setupPlan.steps.isEmpty {
                Text("No setup steps reported yet — the daemon is still gathering readiness. Give it a moment or retry.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else {
                ForEach(model.setupPlan.steps.sorted { $0.order < $1.order }) { planStep in
                    OnboardingStatusRow(
                        title: planStep.title,
                        detail: planStep.detail,
                        ok: planStep.status.lowercased() == "ok",
                        blocking: planStep.blocking,
                    )
                }
            }
            ForEach(model.setupChecks) { check in
                if !check.actions.isEmpty, check.status.lowercased() != "ok" {
                    HStack {
                        Label(check.title, systemImage: check.systemImage)
                            .font(.subheadline)
                        Spacer()
                        ForEach(check.actions) { action in
                            Button(action.displayLabel) {
                                Task { await model.runOnboardingSetupAction(action) }
                            }
                            .disabled(!action.enabled)
                        }
                    }
                }
            }
        }
    }

    private var connectStep: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Connect an account (optional)")
                .font(.headline)
            Text("Connect Google (Gmail / Calendar / Drive) to work with real mail and calendar. You can skip this and do it later from Setup.")
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            Button("Connect Google account…") {
                model.presentGoogleOAuthWizard()
                openWindow(id: "google-oauth-wizard")
            }
            .buttonStyle(.borderedProminent)
        }
    }

    private var readyStep: some View {
        VStack(alignment: .leading, spacing: 12) {
            if model.setupPlan.ready {
                Label("You're ready to chat", systemImage: "checkmark.seal.fill")
                    .font(.headline)
                    .foregroundStyle(.green)
                Text("Everything needed for a first turn is in place.")
                    .foregroundStyle(.secondary)
            } else {
                Label("Almost there", systemImage: "exclamationmark.triangle")
                    .font(.headline)
                    .foregroundStyle(.orange)
                Text("Some setup is still incomplete. You can go back and finish it, or start chatting anyway — CapDep will prompt for anything it needs.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                ForEach(model.setupPlan.steps.filter { $0.blocking && $0.status.lowercased() != "ok" }) { planStep in
                    OnboardingStatusRow(title: planStep.title, detail: planStep.detail, ok: false, blocking: true)
                }
            }
        }
    }

    private var footer: some View {
        HStack {
            if step != .welcome {
                Button("Back") { back() }
            }
            Spacer()
            Button("Skip setup") {
                model.completeOnboarding()
            }
            .foregroundStyle(.secondary)

            if step == .ready {
                Button("Start chatting") {
                    model.completeOnboarding()
                    openWindow(id: "main")
                }
                .buttonStyle(.borderedProminent)
                .keyboardShortcut(.defaultAction)
            } else {
                Button("Next") { advance() }
                    .buttonStyle(.borderedProminent)
                    .keyboardShortcut(.defaultAction)
                    .disabled(!canAdvance)
            }
        }
        .controlSize(.large)
    }

    /// Gate forward movement on the real state each step represents.
    private var canAdvance: Bool {
        switch step {
        case .welcome: return true
        case .daemon: return model.connected
        case .setup: return !OnboardingLogic.blockingStepsRemain(model.setupPlan.steps)
        case .connect: return true
        case .ready: return true
        }
    }

    private func advance() {
        if let next = Step(rawValue: step.rawValue + 1) {
            step = next
        }
    }

    private func back() {
        if let prev = Step(rawValue: step.rawValue - 1) {
            step = prev
        }
    }
}

private struct OnboardingStatusRow: View {
    let title: String
    var detail: String = ""
    let ok: Bool
    var blocking: Bool = false

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: ok ? "checkmark.circle.fill" : (blocking ? "exclamationmark.circle.fill" : "circle"))
                .foregroundStyle(ok ? .green : (blocking ? .orange : .secondary))
            VStack(alignment: .leading, spacing: 2) {
                Text(title.isEmpty ? "(step)" : title)
                    .font(.subheadline.weight(.medium))
                if !detail.isEmpty {
                    Text(detail)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer()
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.quaternary.opacity(0.25), in: RoundedRectangle(cornerRadius: 8))
    }
}
