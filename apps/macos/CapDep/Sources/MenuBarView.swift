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
                    Text(model.connected ? "CapDep protected" : "Daemon offline")
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
                openWindow(id: "command-palette")
            } label: {
                Label("New Ask", systemImage: "sparkle.magnifyingglass")
            }
            .keyboardShortcut(" ", modifiers: [.option])

            Button {
                model.selectedSection = .approvals
                openWindow(id: "main")
            } label: {
                Label("Approvals", systemImage: "hand.raised")
            }

            Button {
                openWindow(id: "task-panel")
            } label: {
                Label("Current Task Panel", systemImage: "sidebar.right")
            }

            Button {
                openWindow(id: "main")
            } label: {
                Label("Dashboard", systemImage: "rectangle.3.group")
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
                            openWindow(id: "task-panel")
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
                            model.selectedSection = .approvals
                            openWindow(id: "main")
                        }
                    }
                }
            }

            Divider()

            HStack {
                Button("Refresh") {
                    Task {
                        await model.refresh()
                    }
                }
                Spacer()
                Button("Quit") {
                    NSApplication.shared.terminate(nil)
                }
            }
        }
        .padding()
        .frame(width: 360)
    }
}
