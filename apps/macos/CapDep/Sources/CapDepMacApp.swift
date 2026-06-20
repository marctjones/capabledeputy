import SwiftUI

@main
struct CapDepMacApp: App {
    @StateObject private var model = CapDepAppModel()

    var body: some Scene {
        MenuBarExtra {
            MenuBarView()
                .environmentObject(model)
                .task {
                    await model.refresh()
                }
        } label: {
            Label("CapDep", systemImage: model.pendingApprovals.isEmpty ? "shield" : "shield.lefthalf.filled")
        }

        WindowGroup("CapDep", id: "main") {
            DashboardView()
                .environmentObject(model)
                .frame(minWidth: 1040, minHeight: 720)
                .task {
                    await model.start()
                }
        }

        Window("Ask CapDep", id: "command-palette") {
            CommandPaletteView()
                .environmentObject(model)
                .frame(width: 720, height: 520)
                .task {
                    await model.start()
                }
        }
        .windowResizability(.contentSize)

        Window("Current Task", id: "task-panel") {
            TaskPanelView()
                .environmentObject(model)
                .frame(width: 460, height: 620)
                .task {
                    await model.start()
                }
        }
        .windowResizability(.contentSize)

        Settings {
            CapDepSettingsView()
                .environmentObject(model)
                .frame(width: 760, height: 560)
                .task {
                    await model.start()
                }
        }

        .commands {
            CapDepCommands(model: model)
        }
    }
}

struct CapDepCommands: Commands {
    @ObservedObject var model: CapDepAppModel
    @Environment(\.openWindow) private var openWindow

    var body: some Commands {
        CommandGroup(after: .newItem) {
            Button("New Ask") {
                openWindow(id: "command-palette")
            }
            .keyboardShortcut(" ", modifiers: [.option])

            Button("New Session") {
                Task {
                    await model.createSession(intent: "New CapDep session")
                }
            }
            .keyboardShortcut("n", modifiers: [.command])

            Menu("New Purpose Session") {
                ForEach(Purpose.allCases) { purpose in
                    Button(purpose.rawValue.capitalized) {
                        Task {
                            await model.createSession(
                                intent: "\(purpose.rawValue.capitalized) session",
                                purpose: purpose,
                            )
                        }
                    }
                }
            }

            Button("Open Dashboard") {
                openWindow(id: "main")
            }
            .keyboardShortcut("0", modifiers: [.command])

            Button("Open Approval Queue") {
                model.selectedSection = .approvals
                openWindow(id: "main")
            }
            .keyboardShortcut("a", modifiers: [.command, .shift])
        }

        CommandMenu("Session") {
            Button("Fork Clean Session") {
                Task {
                    await model.createSession(intent: "Clean recovery session")
                }
            }
            Button("Set Purpose: Inbox") {
                model.selectedPurpose = .inbox
            }
            Button("Set Purpose: Calendar") {
                model.selectedPurpose = .calendar
            }
            Button("Set Purpose: Writing") {
                model.selectedPurpose = .writing
            }
            Button("Set Purpose: Research") {
                model.selectedPurpose = .research
            }
        }

        CommandMenu("Approvals") {
            Button("Show Pending Approvals") {
                model.selectedSection = .approvals
                openWindow(id: "main")
            }
            Button("Deny Selected Approval") {
                Task {
                    if let approval = model.pendingApprovals.first {
                        await model.deny(approval)
                    }
                }
            }
            .disabled(model.pendingApprovals.isEmpty)
        }

        CommandMenu("Workflows") {
            ForEach(model.workflows) { workflow in
                Button(workflow.title) {
                    Task {
                        await model.launchWorkflow(workflow)
                        openWindow(id: "task-panel")
                    }
                }
            }
        }

        CommandMenu("Automation") {
            Button("Pause All Automation") {
                model.taskPanelPinned = false
            }
            Button("Show Current Task Panel") {
                openWindow(id: "task-panel")
            }
            Divider()
            Button("Manage App Permissions") {
                model.selectedSection = .setup
                openWindow(id: "main")
            }
            Button("Enable Screen Control for This Session") {
                model.selectedSection = .setup
                openWindow(id: "main")
            }
        }

        CommandMenu("Trust") {
            Button("Relationship Groups") {
                model.selectedSection = .trust
                openWindow(id: "main")
            }
            Button("Approval Patterns") {
                model.selectedSection = .trust
                openWindow(id: "main")
            }
            Button("Validate Configuration") {
                model.selectedSection = .setup
                openWindow(id: "main")
            }
        }

        CommandGroup(after: .help) {
            Button("Why Was This Blocked?") {
                model.selectedSection = .policyTrace
                openWindow(id: "main")
            }
        }
    }
}
