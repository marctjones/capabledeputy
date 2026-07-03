import AppKit
import SwiftUI

struct CapDepSettingsView: View {
    @EnvironmentObject private var model: CapDepAppModel

    var body: some View {
        TabView {
            GeneralSettingsView()
                .environmentObject(model)
                .tabItem {
                    Label("General", systemImage: "gearshape")
                }

            AssistantSettingsView()
                .environmentObject(model)
                .tabItem {
                    Label("Assistant", systemImage: "cpu")
                }

            AccountsSettingsView()
                .environmentObject(model)
                .tabItem {
                    Label("Accounts", systemImage: "person.crop.circle")
                }

            AutomationSettingsView()
                .environmentObject(model)
                .tabItem {
                    Label("Automation", systemImage: "wand.and.stars")
                }

            TrustSettingsView()
                .environmentObject(model)
                .tabItem {
                    Label("Trust", systemImage: "person.2.badge.gearshape")
                }

            AdvancedSettingsView()
                .environmentObject(model)
                .tabItem {
                    Label("Advanced", systemImage: "slider.horizontal.3")
                }
        }
        .padding(20)
    }
}

private struct GeneralSettingsView: View {
    @EnvironmentObject private var model: CapDepAppModel

    var body: some View {
        Form {
            Toggle(
                "Launch CapDep at login",
                isOn: settingBinding(\.launchAtLogin),
            )
            Picker("Default purpose", selection: purposeBinding()) {
                ForEach(Purpose.allCases) { purpose in
                    Text(purpose.rawValue.capitalized).tag(purpose)
                }
            }
            TextField("Global shortcut", text: shortcutBinding())
            Toggle(
                "Show notifications for pending approvals",
                isOn: settingBinding(\.notificationsEnabled),
            )
        }
        .formStyle(.grouped)
    }

    private func settingBinding(_ keyPath: WritableKeyPath<DaemonSettings, Bool>) -> Binding<Bool> {
        Binding {
            model.daemonSettings[keyPath: keyPath]
        } set: { value in
            var updated = model.daemonSettings
            updated[keyPath: keyPath] = value
            Task {
                await model.updateSettings(updated)
            }
        }
    }

    private func purposeBinding() -> Binding<Purpose> {
        Binding {
            Purpose(rawValue: model.daemonSettings.defaultPurpose) ?? .general
        } set: { value in
            var updated = model.daemonSettings
            updated.defaultPurpose = value.rawValue
            Task {
                await model.updateSettings(updated)
            }
        }
    }

    private func shortcutBinding() -> Binding<String> {
        Binding {
            model.daemonSettings.globalShortcut
        } set: { value in
            var updated = model.daemonSettings
            updated.globalShortcut = value
            Task {
                await model.updateSettings(updated)
            }
        }
    }
}

private struct AssistantSettingsView: View {
    @EnvironmentObject private var model: CapDepAppModel

    var body: some View {
        Form {
            LabeledContent("Daemon", value: model.connected ? "Connected" : "Offline")
            LabeledContent("Socket", value: model.client.socketPath)
            LabeledContent("Model backend", value: "Daemon configured")
            Toggle(
                "Prefer local MLX when available",
                isOn: settingBinding(\.preferLocalMLX),
            )
            Toggle(
                "Show thinking output when model supports it",
                isOn: settingBinding(\.showThinkingOutput),
            )
            Section("Local image generation") {
                Picker("Image profile", selection: imageProfileBinding()) {
                    if model.imageProfiles.isEmpty {
                        Text(model.daemonSettings.imageProfile).tag(model.daemonSettings.imageProfile)
                    }
                    ForEach(model.imageProfiles) { profile in
                        Text(profile.displayTitle).tag(profile.id)
                    }
                }
                LabeledContent("Backend", value: model.imageReadiness.backend)
                LabeledContent("Model", value: model.imageReadiness.model)
                LabeledContent("Status", value: model.imageReadiness.ok ? "Ready" : "Needs attention")
                ForEach(model.imageReadiness.checks.filter { $0.status != "ok" }) { check in
                    VStack(alignment: .leading, spacing: 3) {
                        Text("\(check.id): \(check.detail)")
                            .font(.caption)
                        if !check.recovery.isEmpty {
                            Text(check.recovery)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            }
        }
        .formStyle(.grouped)
    }

    private func settingBinding(_ keyPath: WritableKeyPath<DaemonSettings, Bool>) -> Binding<Bool> {
        Binding {
            model.daemonSettings[keyPath: keyPath]
        } set: { value in
            var updated = model.daemonSettings
            updated[keyPath: keyPath] = value
            Task {
                await model.updateSettings(updated)
            }
        }
    }

    private func imageProfileBinding() -> Binding<String> {
        Binding {
            model.daemonSettings.imageProfile
        } set: { value in
            Task {
                await model.selectImageProfile(value)
            }
        }
    }
}

private struct AccountsSettingsView: View {
    @EnvironmentObject private var model: CapDepAppModel
    @Environment(\.openWindow) private var openWindow
    @State private var googleClientIDs: [String: String] = [:]
    @State private var googleClientSecrets: [String: String] = [:]

    var body: some View {
        Form {
            Section("Google Workspace") {
                Text("Use the guided wizard to sign in with Google in your browser. One-time OAuth client setup is only needed the first time.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Button {
                    model.presentGoogleOAuthWizard()
                    openWindow(id: "google-oauth-wizard")
                } label: {
                    Label("Set Up Google Account…", systemImage: "person.crop.circle.badge.plus")
                }
            }
            ForEach(model.connectorStatuses.filter { $0.id.hasPrefix("google-") }) { connector in
                GoogleOAuthSetupView(
                    connector: connector,
                    clientID: binding(for: connector.id, in: $googleClientIDs),
                    clientSecret: binding(for: connector.id, in: $googleClientSecrets),
                )
                .environmentObject(model)
            }
            ForEach(model.connectorStatuses.filter { !$0.id.hasPrefix("google-") }) { connector in
                SetupProviderRow(
                    name: connector.name,
                    status: connector.detail,
                    action: connector.actions.first,
                )
                .environmentObject(model)
            }
        }
        .formStyle(.grouped)
    }

    private func binding(
        for key: String,
        in storage: Binding<[String: String]>,
    ) -> Binding<String> {
        Binding {
            storage.wrappedValue[key] ?? ""
        } set: { value in
            storage.wrappedValue[key] = value
        }
    }
}

private struct GoogleOAuthSetupView: View {
    @EnvironmentObject private var model: CapDepAppModel
    let connector: ConnectorStatus
    @Binding var clientID: String
    @Binding var clientSecret: String

    var body: some View {
        Section("\(connector.name) MCP") {
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    VStack(alignment: .leading, spacing: 3) {
                        Text(connector.name)
                        Text(statusText)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    statusBadge
                }

                TextField("OAuth client ID", text: $clientID)
                    .textContentType(.username)
                SecureField("OAuth client secret", text: $clientSecret)
                    .textContentType(.password)

                HStack {
                    Button("Save OAuth Client") {
                        Task {
                            await model.configureGoogleOAuth(
                                serviceID: connector.id,
                                clientID: clientID,
                                clientSecret: clientSecret,
                            )
                            clientSecret = ""
                        }
                    }
                    .disabled(
                        model.isConfiguringGoogleOAuth
                            || clientID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                            || clientSecret.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                    )

                    Button("Authorize") {
                        Task {
                            await model.authorizeGoogleOAuth(serviceID: connector.id)
                        }
                    }
                    .disabled(
                        model.isConfiguringGoogleOAuth
                            || !status.clientIDConfigured
                            || !status.clientSecretConfigured,
                    )

                    Button("Revoke Token") {
                        Task {
                            await model.revokeGoogleOAuth(serviceID: connector.id)
                        }
                    }
                    .disabled(model.isConfiguringGoogleOAuth || !status.tokenConfigured)

                    if model.isConfiguringGoogleOAuth {
                        ProgressView()
                            .controlSize(.small)
                    }
                }

                if !status.serverYAML.isEmpty {
                    Text("Server config: \(status.serverYAML)")
                        .font(.caption.monospaced())
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                }
                if status.restartRequired
                    && status.configured
                    && !model.appStatus.upstreamServers.contains(where: { $0.name == connector.id })
                {
                    Text("Restart the daemon after authorization so \(connector.name) MCP is loaded into the tool registry.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        }
    }

    private var status: GoogleOAuthStatus {
        model.googleOAuthStatus(for: connector.id)
    }

    private var statusText: String {
        if status.tokenConfigured {
            return "OAuth token cache exists. \(connector.name) MCP can load after daemon restart."
        }
        if status.clientIDConfigured && status.clientSecretConfigured {
            return "OAuth client saved by the daemon. Authorize \(connector.name) next."
        }
        return "Enter the OAuth client ID and secret from your Google Cloud OAuth client."
    }

    private var statusBadge: some View {
        let text: String
        let color: Color
        if status.tokenConfigured {
            text = "Authorized"
            color = .green
        } else if status.clientIDConfigured {
            text = "Client Saved"
            color = .yellow
        } else {
            text = "Not Configured"
            color = .secondary
        }
        return Text(text)
            .font(.caption.weight(.semibold))
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(color.opacity(0.16), in: Capsule())
            .foregroundStyle(color)
    }
}

private struct AutomationSettingsView: View {
    @EnvironmentObject private var model: CapDepAppModel

    var body: some View {
        Form {
            Toggle(
                "Enable generic screen control for this session",
                isOn: settingBinding(\.enableScreenControl),
            )
            Text("Generic screen control is intentionally high-friction. Prefer MCP/API connectors and app-specific AppleScript tools.")
                .foregroundStyle(.secondary)
            ForEach(model.connectorStatuses.filter { $0.type == "local_app" }) { connector in
                SetupProviderRow(
                    name: connector.name,
                    status: connector.detail,
                    action: connector.actions.first,
                )
                .environmentObject(model)
            }
        }
        .formStyle(.grouped)
    }

    private func settingBinding(_ keyPath: WritableKeyPath<DaemonSettings, Bool>) -> Binding<Bool> {
        Binding {
            model.daemonSettings[keyPath: keyPath]
        } set: { value in
            var updated = model.daemonSettings
            updated[keyPath: keyPath] = value
            Task {
                await model.updateSettings(updated)
            }
        }
    }
}

private struct TrustSettingsView: View {
    @EnvironmentObject private var model: CapDepAppModel

    var body: some View {
        Form {
            Toggle(
                "Require Touch ID for high-risk approvals",
                isOn: settingBinding(\.requireTouchIDForHighRisk),
            )
            SetupProviderRow(
                name: "Relationship groups",
                status: "Edit self/family/work/trusted recipients from the Trust dashboard.",
                action: nil,
            )
            .environmentObject(model)
            SetupProviderRow(
                name: "Approval patterns",
                status: "Create narrow recurring approvals from reviewed actions in the Trust dashboard.",
                action: nil,
            )
            .environmentObject(model)
            SetupProviderRow(
                name: "Source bindings",
                status: "\(model.sourceBindings.count) daemon-owned binding(s) loaded.",
                action: nil,
            )
            .environmentObject(model)
        }
        .formStyle(.grouped)
    }

    private func settingBinding(_ keyPath: WritableKeyPath<DaemonSettings, Bool>) -> Binding<Bool> {
        Binding {
            model.daemonSettings[keyPath: keyPath]
        } set: { value in
            var updated = model.daemonSettings
            updated[keyPath: keyPath] = value
            Task {
                await model.updateSettings(updated)
            }
        }
    }
}

private struct AdvancedSettingsView: View {
    @EnvironmentObject private var model: CapDepAppModel

    var body: some View {
        Form {
            LabeledContent("Socket path", value: model.client.socketPath)
            LabeledContent("Daemon config", value: model.configValidation.configPath.isEmpty ? "(none)" : model.configValidation.configPath)
            Toggle(
                "Verbose daemon logging",
                isOn: settingBinding(\.verboseDaemonLogging),
            )
            Button("Validate Configuration") {
                Task {
                    await model.validateConfiguration()
                }
            }
            Button("Open Logs Folder") {
                Task {
                    await model.refreshLogLocations()
                    openFirstLogDirectory()
                }
            }
            ForEach(model.configValidation.issues) { issue in
                Text("\(issue.severity): \(issue.subject) \(issue.message)")
                    .font(.caption)
                    .foregroundStyle(issue.severity == "error" ? .red : .secondary)
            }
            ForEach(model.logLocations) { location in
                LabeledContent(location.title, value: location.path)
            }
        }
        .formStyle(.grouped)
    }

    private func settingBinding(_ keyPath: WritableKeyPath<DaemonSettings, Bool>) -> Binding<Bool> {
        Binding {
            model.daemonSettings[keyPath: keyPath]
        } set: { value in
            var updated = model.daemonSettings
            updated[keyPath: keyPath] = value
            Task {
                await model.updateSettings(updated)
            }
        }
    }

    private func openFirstLogDirectory() {
        guard let path = model.logLocations.first(where: { !$0.path.isEmpty })?.path else {
            return
        }
        let url = URL(fileURLWithPath: path).deletingLastPathComponent()
        NSWorkspace.shared.open(url)
    }
}

private struct SetupProviderRow: View {
    @EnvironmentObject private var model: CapDepAppModel
    let name: String
    let status: String
    let action: SetupAction?

    var body: some View {
        HStack {
            VStack(alignment: .leading) {
                Text(name)
                Text(status)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            if let action {
                Button(action.label) {
                    Task {
                        await model.runSetupAction(action)
                    }
                }
                .disabled(!action.enabled)
            } else {
                Text("Managed")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }
}
