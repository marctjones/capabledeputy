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

private struct AccountsSettingsView: View {
    @EnvironmentObject private var model: CapDepAppModel
    @State private var googleClientID = ""
    @State private var googleClientSecret = ""

    var body: some View {
        Form {
            GmailOAuthSetupView(
                clientID: $googleClientID,
                clientSecret: $googleClientSecret,
            )
            .environmentObject(model)
            ForEach(model.connectorStatuses.filter { $0.id != "google-gmail" }) { connector in
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
}

private struct GmailOAuthSetupView: View {
    @EnvironmentObject private var model: CapDepAppModel
    @Binding var clientID: String
    @Binding var clientSecret: String

    var body: some View {
        Section("Google Gmail MCP") {
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    VStack(alignment: .leading, spacing: 3) {
                        Text("Google Gmail")
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
                            await model.configureGmailOAuth(
                                clientID: clientID,
                                clientSecret: clientSecret,
                            )
                            clientSecret = ""
                        }
                    }
                    .disabled(
                        model.isConfiguringGmailOAuth
                            || clientID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                            || clientSecret.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                    )

                    Button("Authorize Gmail") {
                        Task {
                            await model.authorizeGmailOAuth()
                        }
                    }
                    .disabled(
                        model.isConfiguringGmailOAuth
                            || !model.gmailOAuthStatus.clientIDConfigured
                            || !model.gmailOAuthStatus.clientSecretConfigured,
                    )

                    if model.isConfiguringGmailOAuth {
                        ProgressView()
                            .controlSize(.small)
                    }
                }

                if !model.gmailOAuthStatus.serverYAML.isEmpty {
                    Text("Server config: \(model.gmailOAuthStatus.serverYAML)")
                        .font(.caption.monospaced())
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                }
                if model.gmailOAuthStatus.restartRequired
                    && model.gmailOAuthStatus.configured
                    && !model.appStatus.upstreamServers.contains(where: { $0.name == "google-gmail" })
                {
                    Text("Restart the daemon after authorization so Gmail MCP is loaded into the tool registry.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        }
    }

    private var statusText: String {
        if model.gmailOAuthStatus.tokenConfigured {
            return "OAuth token cache exists. Gmail MCP can load after daemon restart."
        }
        if model.gmailOAuthStatus.clientIDConfigured && model.gmailOAuthStatus.clientSecretConfigured {
            return "OAuth client saved by the daemon. Authorize Gmail next."
        }
        return "Enter the OAuth client ID and secret from your Google Cloud OAuth client."
    }

    private var statusBadge: some View {
        let text: String
        let color: Color
        if model.gmailOAuthStatus.tokenConfigured {
            text = "Authorized"
            color = .green
        } else if model.gmailOAuthStatus.clientIDConfigured {
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
