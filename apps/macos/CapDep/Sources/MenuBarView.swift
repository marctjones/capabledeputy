import SwiftUI

struct MenuBarView: View {
    @EnvironmentObject private var model: CapDepAppModel
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 10) {
                Image(systemName: model.connected ? "checkmark.shield" : "xmark.octagon")
                    .foregroundStyle(model.connected ? .green : .red)
                    .font(.title3)
                VStack(alignment: .leading) {
                    Text(statusTitle)
                        .font(.headline)
                    Text("Purpose: \(model.selectedPurpose.rawValue.capitalized)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                if !model.pendingApprovals.isEmpty {
                    Text("\(model.pendingApprovals.count)")
                        .font(.caption.weight(.bold))
                        .foregroundStyle(.white)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 4)
                        .background(.yellow, in: Capsule())
                }
            }

            if let error = model.lastError {
                Text(error)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(3)
            }

            Divider()

            Button {
                openWindow(id: "main")
            } label: {
                Label("Open Chat", systemImage: "bubble.left.and.bubble.right")
            }
            .keyboardShortcut(" ", modifiers: [.option])

            Button {
                if let first = model.pendingApprovals.first {
                    model.presentApproval(id: first.id)
                    openWindow(id: "approval-card")
                } else {
                    model.selectedSection = .approvals
                    openWindow(id: "console")
                }
            } label: {
                Label("Approvals", systemImage: "hand.raised")
            }

            Button {
                openWindow(id: "console")
            } label: {
                Label("Console", systemImage: "rectangle.3.group")
            }

            Divider()

            VStack(alignment: .leading, spacing: 8) {
                Text("Quick Workflows")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                ForEach(model.workflows.prefix(4)) { workflow in
                    Button {
                        Task {
                            await model.launchWorkflow(workflow)
                            openWindow(id: "main")
                        }
                    } label: {
                        Label(workflow.title, systemImage: workflow.systemImage)
                    }
                }
            }

            if !model.pendingApprovals.isEmpty {
                Divider()
                VStack(alignment: .leading, spacing: 8) {
                    Text("Pending")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.secondary)
                    ForEach(model.pendingApprovals.prefix(3)) { approval in
                        Button("#\(approval.id) \(approval.action) -> \(approval.target)") {
                            model.presentApproval(id: approval.id)
                            openWindow(id: "approval-card")
                        }
                    }
                }
            }

            if needsGoogleSetup {
                Divider()
                Button {
                    model.presentGoogleOAuthWizard()
                    openWindow(id: "google-oauth-wizard")
                } label: {
                    Label("Set Up Google Account…", systemImage: "person.crop.circle.badge.plus")
                }
            }

            Divider()

            HStack {
                if model.isRecoveringDaemon {
                    ProgressView()
                        .controlSize(.small)
                    Text("Recovering daemon...")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button("Refresh") {
                    Task {
                        await model.refresh()
                    }
                }
                Button("Quit") {
                    NSApplication.shared.terminate(nil)
                }
            }
        }
        .padding()
        .frame(width: 360)
        .onChange(of: model.isGoogleOAuthWizardPresented) { _, presented in
            if presented {
                openWindow(id: "google-oauth-wizard")
            }
        }
    }

    private var needsGoogleSetup: Bool {
        model.setupChecks.contains(where: { $0.id == "google-oauth" && $0.status != "ok" })
            || model.connectorStatuses.contains(where: {
                $0.id.hasPrefix("google-") && $0.status != "connected"
            })
    }

    private var statusTitle: String {
        if model.isRecoveringDaemon {
            return "Starting daemon"
        }
        return model.connected ? "CapDep protected" : "Daemon offline"
    }
}
