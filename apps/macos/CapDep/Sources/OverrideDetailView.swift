import SwiftUI

/// #331 — the GUI OVERRIDE_REQUIRED control. Before this, override was
/// CLI-only (`/override request|attest|list|show|refuse`); the model held
/// `overrideGrants`/`showOverride`/`refuseOverride` with zero view consumers
/// and no request path. This card makes an override-gated action fully
/// resolvable in CapDepMac: request (dual-control step 1), list, show,
/// attest by a distinct principal (step 2), and refuse — all over the daemon
/// override RPCs, no terminal required.
struct OverrideCardWindow: View {
    @EnvironmentObject private var model: CapDepAppModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        Group {
            if model.overrideWindowID == nil {
                ContentUnavailableView(
                    "No Override Open",
                    systemImage: "hand.raised",
                    description: Text("This action requested no override, or it was already resolved."),
                )
            } else {
                OverrideDetailView()
            }
        }
        .onChange(of: model.overrideWindowID) { _, newValue in
            if newValue == nil {
                dismiss()
            }
        }
    }
}

struct OverrideDetailView: View {
    @EnvironmentObject private var model: CapDepAppModel
    @State private var draft = OverrideRequestDraft()
    @State private var attester = ""
    @State private var didPrefill = false

    private var selectedGrant: OverrideGrantViewData? {
        guard let id = model.overrideWindowID, !id.isEmpty else { return nil }
        return model.overrideGrants.first { $0.id == id }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                header

                if let grant = selectedGrant {
                    OverrideGrantDetail(grant: grant, attester: $attester)
                } else {
                    OverrideRequestForm(draft: $draft, pendingHint: model.pendingOverrideRequired)
                }

                if !model.overrideGrants.isEmpty {
                    OverrideSection(title: "Grants") {
                        VStack(spacing: 8) {
                            ForEach(model.overrideGrants) { grant in
                                OverrideGrantRow(
                                    grant: grant,
                                    isSelected: grant.id == model.overrideWindowID,
                                ) {
                                    model.presentOverrideGrant(grant)
                                }
                            }
                        }
                    }
                }
            }
            .padding(28)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .task {
            // Prefill the request form once from the pending turn / session.
            if !didPrefill, selectedGrant == nil {
                draft = model.overrideDraftFromPendingTurn()
                didPrefill = true
            }
        }
    }

    private var header: some View {
        HStack(alignment: .firstTextBaseline) {
            VStack(alignment: .leading, spacing: 6) {
                Text(selectedGrant == nil ? "Request Override" : "Override Grant")
                    .font(.title.weight(.semibold))
                Text("Crossing a hard floor requires an explicit, dual-controlled override.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            if let grant = selectedGrant {
                OverrideStatePill(state: grant.state)
            }
        }
    }
}

private struct OverrideRequestForm: View {
    @EnvironmentObject private var model: CapDepAppModel
    @Binding var draft: OverrideRequestDraft
    let pendingHint: ToolOutcome?

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            if let hint = pendingHint {
                OverrideSection(title: "This Turn Was Gated") {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Tool: \(hint.toolName.isEmpty ? "(unknown)" : hint.toolName)")
                            .font(.caption.monospaced())
                        if !hint.rule.isEmpty {
                            Text("Rule: \(hint.rule)")
                                .font(.caption.monospaced())
                                .foregroundStyle(.secondary)
                        }
                        Text("Fill the action kind and floor being crossed below (a tool name like \"email.send\" is not the CapabilityKind \"SEND_EMAIL\").")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                }
            }

            OverrideSection(title: "Request") {
                VStack(alignment: .leading, spacing: 10) {
                    OverrideField(label: "Session id", text: $draft.sessionID)
                    OverrideField(label: "Action kind (CapabilityKind, e.g. SEND_EMAIL)", text: $draft.actionKind)
                    OverrideField(label: "Target (recipient / pattern)", text: $draft.target)
                    OverrideField(label: "Hard floor being crossed", text: $draft.floor)
                    OverrideField(label: "Invoker principal", text: $draft.invoker)
                    HStack(spacing: 12) {
                        OverrideField(label: "Category", text: $draft.category)
                        OverrideField(label: "Tier", text: $draft.tier)
                    }
                    Toggle("Friction confirmed (required for single-authorized + maximal floors)", isOn: $draft.frictionConfirmed)
                        .font(.caption)
                }
            }

            HStack {
                Spacer()
                Button {
                    Task { await model.requestOverride(draft) }
                } label: {
                    Label("Request Override", systemImage: "hand.raised")
                }
                .buttonStyle(.borderedProminent)
                .disabled(!draft.isSubmittable)
                .keyboardShortcut(.defaultAction)
            }
            .controlSize(.large)
        }
    }
}

private struct OverrideGrantDetail: View {
    @EnvironmentObject private var model: CapDepAppModel
    let grant: OverrideGrantViewData
    @Binding var attester: String

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            OverrideSection(title: "Grant") {
                VStack(alignment: .leading, spacing: 6) {
                    OverrideKeyValue(key: "id", value: grant.id)
                    OverrideKeyValue(key: "session", value: grant.sessionID)
                    OverrideKeyValue(key: "action", value: grant.actionKind)
                    OverrideKeyValue(key: "target", value: grant.target)
                    OverrideKeyValue(key: "invoker", value: grant.invokerPrincipal)
                    OverrideKeyValue(key: "state", value: grant.state)
                    OverrideKeyValue(key: "expires", value: grant.expiresAt)
                }
            }

            if grant.awaitsAttestation {
                OverrideSection(title: "Attestation (distinct principal)") {
                    VStack(alignment: .leading, spacing: 10) {
                        Text("Dual control: a second, distinct principal must confirm before the grant becomes usable.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        OverrideField(label: "Attester principal", text: $attester)
                    }
                }
            }

            HStack {
                Button(role: .destructive) {
                    Task { await model.refuseOverride(grant) }
                } label: {
                    Label("Refuse", systemImage: "xmark.circle")
                }
                .keyboardShortcut(.cancelAction)

                Button("Back to Request") {
                    model.presentOverrideRequest()
                }

                Spacer()

                if grant.awaitsAttestation {
                    Button {
                        Task { await model.attestOverride(grant, attester: attester, confirm: true) }
                    } label: {
                        Label("Attest & Confirm", systemImage: "checkmark.seal")
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(attester.trimmingCharacters(in: .whitespaces).isEmpty)
                    .keyboardShortcut(.defaultAction)
                }
            }
            .controlSize(.large)
        }
    }
}

private struct OverrideGrantRow: View {
    let grant: OverrideGrantViewData
    let isSelected: Bool
    let onSelect: () -> Void

    var body: some View {
        Button(action: onSelect) {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text(grant.actionKind.isEmpty ? "(action)" : grant.actionKind)
                        .font(.subheadline.weight(.semibold))
                    Text(grant.target.isEmpty ? "(no target)" : grant.target)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                OverrideStatePill(state: grant.state)
            }
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                (isSelected ? Color.accentColor.opacity(0.16) : Color.gray.opacity(0.08)),
                in: RoundedRectangle(cornerRadius: 8),
            )
        }
        .buttonStyle(.plain)
    }
}

// MARK: - Small shared building blocks (scoped to the override card)

private struct OverrideSection<Content: View>: View {
    let title: String
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(.headline)
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct OverrideField: View {
    let label: String
    @Binding var text: String

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(label)
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
            TextField(label, text: $text)
                .textFieldStyle(.roundedBorder)
        }
    }
}

private struct OverrideKeyValue: View {
    let key: String
    let value: String

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(key)
                .font(.caption.weight(.semibold).monospaced())
                .foregroundStyle(.secondary)
                .frame(width: 72, alignment: .leading)
            Text(value.isEmpty ? "—" : value)
                .font(.caption.monospaced())
                .textSelection(.enabled)
        }
    }
}

private struct OverrideStatePill: View {
    let state: String

    var body: some View {
        Text(state.isEmpty ? "UNKNOWN" : state)
            .font(.caption.weight(.semibold))
            .padding(.horizontal, 10)
            .padding(.vertical, 5)
            .background(tint.opacity(0.18), in: Capsule())
    }

    private var tint: Color {
        let s = state.uppercased()
        if s.contains("REFUSED") || s.contains("EXPIRED") { return .red }
        if s.contains("PENDING") { return .orange }
        if s.contains("ACTIVE") || s.contains("GRANTED") { return .green }
        return .blue
    }
}
