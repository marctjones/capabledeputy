import SwiftUI

struct CapDepSettingsView: View {
    @EnvironmentObject private var model: CapDepAppModel
    @State private var launchAtLogin = false
    @State private var requireTouchID = false
    @State private var enableScreenControl = false

    var body: some View {
        TabView {
            GeneralSettingsView(launchAtLogin: $launchAtLogin)
                .tabItem {
                    Label("General", systemImage: "gearshape")
                }

            AssistantSettingsView()
                .environmentObject(model)
                .tabItem {
                    Label("Assistant", systemImage: "cpu")
                }

            AccountsSettingsView()
                .tabItem {
                    Label("Accounts", systemImage: "person.crop.circle")
                }

            AutomationSettingsView(enableScreenControl: $enableScreenControl)
                .tabItem {
                    Label("Automation", systemImage: "wand.and.stars")
                }

            TrustSettingsView(requireTouchID: $requireTouchID)
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
    @Binding var launchAtLogin: Bool

    var body: some View {
        Form {
            Toggle("Launch CapDep at login", isOn: $launchAtLogin)
            Picker("Default purpose", selection: .constant(Purpose.general)) {
                ForEach(Purpose.allCases) { purpose in
                    Text(purpose.rawValue.capitalized).tag(purpose)
                }
            }
            TextField("Global shortcut", text: .constant("Option-Space"))
            Toggle("Show notifications for pending approvals", isOn: .constant(true))
        }
        .formStyle(.grouped)
    }
}

private struct AssistantSettingsView: View {
    @EnvironmentObject private var model: CapDepAppModel

    var body: some View {
        Form {
            LabeledContent("Daemon", value: model.connected ? "Connected" : "Offline")
            LabeledContent("Socket", value: model.client.socketPath)
            LabeledContent("Model backend", value: "Daemon configured")
            Toggle("Prefer local MLX when available", isOn: .constant(true))
            Toggle("Show thinking output when model supports it", isOn: .constant(false))
        }
        .formStyle(.grouped)
    }
}

private struct AccountsSettingsView: View {
    var body: some View {
        Form {
            SetupProviderRow(name: "Google Gmail", status: "OAuth status from daemon pending")
            SetupProviderRow(name: "Google Calendar", status: "OAuth status from daemon pending")
            SetupProviderRow(name: "Google Drive", status: "OAuth status from daemon pending")
            SetupProviderRow(name: "Apple Mail", status: "Uses macOS Automation permission")
        }
        .formStyle(.grouped)
    }
}

private struct AutomationSettingsView: View {
    @Binding var enableScreenControl: Bool

    var body: some View {
        Form {
            Toggle("Enable generic screen control for this session", isOn: $enableScreenControl)
            Text("Generic screen control is intentionally high-friction. Prefer MCP/API connectors and app-specific AppleScript tools.")
                .foregroundStyle(.secondary)
            SetupProviderRow(name: "Mail", status: "Ask when workflow first needs it")
            SetupProviderRow(name: "Pages", status: "Ask when workflow first needs it")
            SetupProviderRow(name: "Numbers", status: "Ask when workflow first needs it")
            SetupProviderRow(name: "Keynote", status: "Ask when workflow first needs it")
        }
        .formStyle(.grouped)
    }
}

private struct TrustSettingsView: View {
    @Binding var requireTouchID: Bool

    var body: some View {
        Form {
            Toggle("Require Touch ID for high-risk approvals", isOn: $requireTouchID)
            SetupProviderRow(name: "Relationship groups", status: "Edit self/family/work/trusted recipients")
            SetupProviderRow(name: "Approval patterns", status: "Create narrow recurring approvals from reviewed actions")
            SetupProviderRow(name: "Source bindings", status: "Label files, apps, and service URI scopes")
        }
        .formStyle(.grouped)
    }
}

private struct AdvancedSettingsView: View {
    @EnvironmentObject private var model: CapDepAppModel

    var body: some View {
        Form {
            LabeledContent("Socket path", value: model.client.socketPath)
            LabeledContent("Daemon config", value: "configs/personal-assistant/daemon.yaml")
            Toggle("Verbose daemon logging", isOn: .constant(false))
            Button("Validate Configuration") {}
            Button("Open Logs Folder") {}
        }
        .formStyle(.grouped)
    }
}

private struct SetupProviderRow: View {
    let name: String
    let status: String

    var body: some View {
        HStack {
            VStack(alignment: .leading) {
                Text(name)
                Text(status)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Button("Open") {}
        }
    }
}
