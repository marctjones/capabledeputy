import SwiftUI

struct GoogleOAuthWizardView: View {
    @EnvironmentObject private var model: CapDepAppModel
    @Environment(\.dismiss) private var dismiss

    @State private var step: WizardStep = .chooseService
    @State private var selectedPresetID = "workspace"
    @State private var selectedServiceID = "google-gmail"
    @State private var clientID = ""
    @State private var clientSecret = ""
    @State private var authorizeError: String?

    private enum WizardStep: Int, CaseIterable {
        case chooseService
        case authorize
        case credentials
        case complete
    }

    private var googleConnectors: [ConnectorStatus] {
        model.connectorStatuses.filter { $0.id.hasPrefix("google-") }
    }

    private var googlePresets: [GoogleOAuthPreset] {
        model.googleOAuthPresets.isEmpty
            ? [
                GoogleOAuthPreset(dictionary: [
                    "id": "gmail",
                    "display_name": "Gmail only",
                    "description": "Connect Gmail for search, reading threads, labels, and drafts.",
                    "service_ids": ["google-gmail"],
                    "grants_summary": "Gmail read/search plus draft and label management.",
                    "next_service_id": "google-gmail",
                ]),
                GoogleOAuthPreset(dictionary: [
                    "id": "workspace",
                    "display_name": "Gmail + Calendar + Drive",
                    "description": "Connect the standard Google Workspace services used by CapDep.",
                    "service_ids": ["google-gmail", "google-calendar", "google-drive"],
                    "grants_summary": "Gmail read/draft/label, read-only Calendar, and read-only Drive.",
                    "next_service_id": "google-gmail",
                ]),
            ]
            : model.googleOAuthPresets
    }

    private var selectedPreset: GoogleOAuthPreset {
        googlePresets.first(where: { $0.id == selectedPresetID }) ?? googlePresets[0]
    }

    private var status: GoogleOAuthStatus {
        model.googleOAuthStatus(for: selectedServiceID)
    }

    private var credentialsReady: Bool {
        status.clientIDConfigured && status.clientSecretConfigured
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            header
            progressBar
            stepContent
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            footer
        }
        .padding(28)
        .frame(minWidth: 560, minHeight: 480)
        .onAppear {
            syncFromModel()
        }
        .onChange(of: model.googleOAuthWizardServiceID) { _, _ in
            syncFromModel()
        }
        .onChange(of: selectedPresetID) { _, _ in
            selectedServiceID = selectedPreset.nextServiceID
            authorizeError = nil
            step = initialStep(for: selectedServiceID)
        }
        .onChange(of: selectedServiceID) { _, _ in
            authorizeError = nil
            step = initialStep(for: selectedServiceID)
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Set Up Google Account")
                .font(.largeTitle.weight(.bold))
            Text("Choose what to connect, then sign in with Google in your browser. CapDep never sees your Google password.")
                .foregroundStyle(.secondary)
        }
    }

    private var progressBar: some View {
        HStack(spacing: 8) {
            ForEach(WizardStep.allCases, id: \.rawValue) { item in
                Capsule()
                    .fill(item.rawValue <= step.rawValue ? Color.accentColor : Color.secondary.opacity(0.25))
                    .frame(height: 4)
            }
        }
    }

    @ViewBuilder
    private var stepContent: some View {
        switch step {
        case .chooseService:
            chooseServiceStep
        case .authorize:
            authorizeStep
        case .credentials:
            credentialsStep
        case .complete:
            completeStep
        }
    }

    private var chooseServiceStep: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Choose a Google account preset")
                .font(.title2.weight(.semibold))
            Text("Pick the Google services CapDep should prepare. Each service stays a separate least-privilege MCP connector under the hood.")
                .foregroundStyle(.secondary)
            Picker("Preset", selection: $selectedPresetID) {
                ForEach(googlePresets) { preset in
                    Text(preset.displayName).tag(preset.id)
                }
            }
            .pickerStyle(.radioGroup)
            presetStatusCard
            if googleConnectors.isEmpty {
                Text("No Google connectors reported by the daemon. Check that CapDep is connected, then refresh.")
                    .foregroundStyle(.secondary)
            } else {
                serviceStatusCard
            }
        }
    }

    private var authorizeStep: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Sign in with Google")
                .font(.title2.weight(.semibold))
            Text(
                "CapDep opens your default browser to Google's sign-in page. "
                    + "Enter your Google username and password there — not in this app.",
            )
            .foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true)

            serviceStatusCard

            if !credentialsReady {
                firstTimeSetupCallout
            }

            if let authorizeError {
                Text(authorizeError)
                    .font(.caption)
                    .foregroundStyle(.red)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Button {
                Task {
                    await attemptBrowserAuthorization()
                }
            } label: {
                Label("Continue in Browser", systemImage: "safari")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .disabled(model.isConfiguringGoogleOAuth || !credentialsReady)

            if model.isConfiguringGoogleOAuth {
                HStack(spacing: 8) {
                    ProgressView()
                        .controlSize(.small)
                    Text("Waiting for browser authorization…")
                        .foregroundStyle(.secondary)
                }
            }
        }
    }

    private var firstTimeSetupCallout: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("One-time setup required", systemImage: "1.circle")
                .font(.headline)
            Text(
                "Use the one-time Google Cloud setup only if this CapDep install does not already "
                    + "have an OAuth client. This is not your Google login — it is how Google knows "
                    + "CapDep on your Mac is allowed to request the selected preset.",
            )
            .font(.caption)
            .foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true)
            Button("Set Up OAuth Client (one time)") {
                authorizeError = nil
                step = .credentials
            }
            .buttonStyle(.bordered)
        }
        .padding()
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.yellow.opacity(0.12), in: RoundedRectangle(cornerRadius: 12))
    }

    private var credentialsStep: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("One-time OAuth client setup")
                .font(.title2.weight(.semibold))
            Text(
                "Create a Google Cloud OAuth client (Desktop or Web app), then paste the client ID "
                    + "and secret here. Advanced users can bring their own client; normal setup uses "
                    + "the preset choice and browser sign-in.",
            )
            .foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true)

            TextField("OAuth client ID", text: $clientID)
                .textFieldStyle(.roundedBorder)
            SecureField("OAuth client secret", text: $clientSecret)
                .textFieldStyle(.roundedBorder)

            Link(
                "Open Google Cloud Console",
                destination: URL(string: "https://console.cloud.google.com/apis/credentials")!,
            )

            if let error = model.lastError, step == .credentials {
                Text(error)
                    .font(.caption)
                    .foregroundStyle(.red)
            }
        }
    }

    private var completeStep: some View {
        VStack(alignment: .leading, spacing: 16) {
            Label("Google account connected", systemImage: "checkmark.circle.fill")
                .font(.title2.weight(.semibold))
                .foregroundStyle(.green)
            Text("\(status.displayName) is authorized. CapDep can now use the \(status.displayName) MCP server after the daemon reloads its tool registry.")
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            serviceStatusCard

            if status.restartRequired {
                Text("Restart the daemon once so this MCP server is loaded or unloaded in the tool registry.")
                    .foregroundStyle(.secondary)
                Button("Restart Daemon") {
                    Task {
                        await model.restartDaemon()
                        await model.refresh()
                    }
                }
                .buttonStyle(.bordered)
            }

            if let next = nextIncompleteServiceID(after: selectedServiceID) {
                Button("Set Up \(displayName(for: next))") {
                    selectedServiceID = next
                    step = initialStep(for: next)
                }
                .buttonStyle(.bordered)
            }
        }
    }

    private var presetStatusCard: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(selectedPreset.displayName)
                .font(.headline)
            Text(selectedPreset.description)
                .font(.caption)
                .foregroundStyle(.secondary)
            Text(selectedPreset.grantsSummary)
                .font(.caption)
                .foregroundStyle(.secondary)
            if selectedPreset.totalCount > 0 {
                Text("\(selectedPreset.connectedCount)/\(selectedPreset.totalCount) services connected")
                    .font(.caption.weight(.medium))
                    .foregroundStyle(selectedPreset.connected ? .green : .secondary)
            }
        }
        .padding()
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 12))
    }

    private var serviceStatusCard: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(status.displayName)
                .font(.headline)
            Text(statusSummary)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .padding()
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 12))
    }

    private var statusSummary: String {
        if status.tokenConfigured {
            return "Authorized. Token cache exists on the daemon."
        }
        if credentialsReady {
            return "Ready to sign in. Click Continue in Browser."
        }
        return "One-time OAuth client setup is required before browser sign-in."
    }

    private var footer: some View {
        HStack {
            Button("Cancel") {
                model.dismissGoogleOAuthWizard()
                dismiss()
            }
            Spacer()
            if step != .chooseService {
                Button("Back") {
                    step = previousStep(step)
                }
            }
            if step == .chooseService {
                Button("Continue") {
                    step = initialStep(for: selectedServiceID)
                }
                .keyboardShortcut(.return, modifiers: [.command])
                .disabled(googleConnectors.isEmpty)
            } else if step == .credentials {
                Button("Save & Sign In") {
                    Task {
                        await model.configureGoogleOAuth(
                            serviceID: selectedServiceID,
                            clientID: clientID,
                            clientSecret: clientSecret,
                        )
                        clientSecret = ""
                        if model.googleOAuthStatus(for: selectedServiceID).clientIDConfigured {
                            authorizeError = nil
                            step = .authorize
                        }
                    }
                }
                .keyboardShortcut(.return, modifiers: [.command])
                .disabled(
                    model.isConfiguringGoogleOAuth
                        || clientID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                        || clientSecret.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                )
            } else if step == .complete {
                Button("Done") {
                    model.dismissGoogleOAuthWizard()
                    dismiss()
                }
                .keyboardShortcut(.return, modifiers: [.command])
            }
        }
    }

    private func syncFromModel() {
        if let preset = model.googleOAuthWizardServiceID, !preset.isEmpty {
            selectedServiceID = preset
        } else if let first = googleConnectors.first?.id {
            selectedServiceID = first
        }
        if let matchingPreset = googlePresets.first(where: { $0.serviceIDs.contains(selectedServiceID) }) {
            selectedPresetID = matchingPreset.id
            selectedServiceID = matchingPreset.nextServiceID
        }
        step = initialStep(for: selectedServiceID)
        let current = model.googleOAuthStatus(for: selectedServiceID)
        if current.clientIDConfigured {
            clientID = ""
        }
    }

    private func initialStep(for serviceID: String) -> WizardStep {
        let current = model.googleOAuthStatus(for: serviceID)
        if current.tokenConfigured {
            return .complete
        }
        return .authorize
    }

    private func previousStep(_ current: WizardStep) -> WizardStep {
        switch current {
        case .chooseService:
            return .chooseService
        case .authorize:
            return .chooseService
        case .credentials:
            return .authorize
        case .complete:
            return .authorize
        }
    }

    private func attemptBrowserAuthorization() async {
        authorizeError = nil
        guard credentialsReady else {
            authorizeError = "Complete the one-time OAuth client setup first."
            return
        }
        let priorError = model.lastError
        await model.authorizeGoogleOAuth(serviceID: selectedServiceID)
        if model.googleOAuthStatus(for: selectedServiceID).tokenConfigured {
            step = .complete
            return
        }
        if let error = model.lastError, error != priorError {
            authorizeError = error
        } else {
            authorizeError = "Browser authorization did not finish. Try again or check the daemon logs."
        }
    }

    private func nextIncompleteServiceID(after serviceID: String) -> String? {
        let presetServiceIDs = selectedPreset.serviceIDs
        let connectors = googleConnectors.filter { presetServiceIDs.contains($0.id) }
        if connectors.isEmpty {
            return googleConnectors.first(where: { !model.googleOAuthStatus(for: $0.id).tokenConfigured })?.id
        }
        guard let index = connectors.firstIndex(where: { $0.id == serviceID }) else {
            return connectors.first(where: { !model.googleOAuthStatus(for: $0.id).tokenConfigured })?.id
        }
        let remaining = connectors[(index + 1)...] + connectors[..<index]
        return remaining.first(where: { !model.googleOAuthStatus(for: $0.id).tokenConfigured })?.id
    }

    private func displayName(for serviceID: String) -> String {
        googleConnectors.first(where: { $0.id == serviceID })?.name
            ?? model.googleOAuthStatus(for: serviceID).displayName
    }
}
