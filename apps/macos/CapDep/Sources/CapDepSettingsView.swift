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

            McpServersSettingsView()
                .environmentObject(model)
                .tabItem {
                    Label("MCP Servers", systemImage: "server.rack")
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
            Section("Build readiness") {
                Label {
                    Text(model.buildReadiness.comparison.title)
                        .font(.headline)
                } icon: {
                    Image(systemName: buildReadinessSymbol)
                        .foregroundStyle(buildReadinessColor)
                }
                Text(model.buildReadiness.comparison.detail)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                if let guidance = model.buildReadiness.comparison.guidance {
                    Text(guidance)
                        .font(.caption)
                }
                DisclosureGroup("Build identities") {
                    LabeledContent("App bundle version", value: shown(model.buildReadiness.app.version))
                    LabeledContent("App executable SHA-256", value: shown(model.buildReadiness.app.executableSHA256))
                    LabeledContent("App source SHA-256", value: shown(model.buildReadiness.app.sourceContentSHA256))
                    if let daemon = model.buildReadiness.daemon {
                        LabeledContent("Daemon package version", value: shown(daemon.version))
                        LabeledContent("Daemon startup revision", value: daemon.revisionLabel)
                        LabeledContent("Daemon source SHA-256", value: shown(daemon.sourceContentHash))
                        LabeledContent("Daemon mtime manifest", value: shown(daemon.manifestHash))
                    } else {
                        LabeledContent("Daemon identity", value: "Unavailable")
                    }
                    Text("App executable and daemon source hashes identify different components and are not compared with each other. The mtime manifest is diagnostic only.")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
                .font(.caption)
            }
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

    private var buildReadinessSymbol: String {
        switch model.buildReadiness.comparison {
        case .current: "checkmark.circle.fill"
        case .mismatch: "exclamationmark.triangle.fill"
        case .unknown: "questionmark.circle"
        }
    }

    private var buildReadinessColor: Color {
        switch model.buildReadiness.comparison {
        case .current: .green
        case .mismatch: .orange
        case .unknown: .secondary
        }
    }

    private func shown(_ value: String) -> String {
        value.isEmpty ? "Unavailable" : value
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

private struct McpServersSettingsView: View {
    @EnvironmentObject private var model: CapDepAppModel

    var body: some View {
        Form {
            Section("Admitted MCP servers") {
                Button("Probe MCP Servers") {
                    Task { await model.probeMcpServers() }
                }
                Text("A probe sends only MCP list-tools requests. It executes no MCP tool and exposes no provider credentials or content.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                ForEach(model.connectorStatuses.filter { $0.type == "mcp_server" }) { connector in
                    SetupProviderRow(
                        name: connector.name,
                        status: connector.detail,
                        action: nil,
                    )
                    .environmentObject(model)
                }
            }

            Section("Desktop application setup") {
                ForEach(model.connectorStatuses.filter { $0.type == "local_app" }) { connector in
                    SetupProviderRow(
                        name: connector.name,
                        status: connector.detail,
                        action: connector.actions.first,
                    )
                    .environmentObject(model)
                }
            }
        }
        .formStyle(.grouped)
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
