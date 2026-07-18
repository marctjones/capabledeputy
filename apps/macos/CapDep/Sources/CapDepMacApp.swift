import SwiftUI
import UserNotifications

/// #332 — the menu-bar label is the one CapDep view that stays alive whenever
/// the app is running (main/console windows can all be closed). Hosting the
/// policy-prompt auto-open observers here — instead of only on ChatView, which
/// dies with the main window — makes approval/grant/override cards reliably
/// surface no matter which window (if any) is open.
struct MenuBarLabel: View {
    @EnvironmentObject private var model: CapDepAppModel
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        Label(
            "CapDep",
            systemImage: model.pendingApprovals.isEmpty ? "shield" : "shield.lefthalf.filled",
        )
        .onChange(of: model.approvalWindowID) { _, newValue in
            if newValue != nil {
                openWindow(id: "approval-card")
            }
        }
        .onChange(of: model.grantPromptPresented) { _, presented in
            if presented {
                openWindow(id: "capability-grant-card")
            }
        }
        .onChange(of: model.overrideWindowID) { _, newValue in
            if newValue != nil {
                openWindow(id: "override-card")
            }
        }
    }
}

struct ApprovalCardWindow: View {
    @EnvironmentObject private var model: CapDepAppModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        Group {
            if let approval = model.pendingApprovals.first(where: { $0.id == model.approvalWindowID }) {
                ApprovalDetailView(approval: approval)
            } else {
                ContentUnavailableView(
                    "No Pending Approval",
                    systemImage: "checkmark.shield",
                    description: Text("The approval queue is empty or this item was already decided."),
                )
            }
        }
        .onChange(of: model.approvalWindowID) { _, newValue in
            if newValue == nil {
                dismiss()
            }
        }
    }
}

@main
struct CapDepMacApp: App {
    private static let singleInstanceGuard = SingleInstanceGuard(name: "capdepmac")
    @StateObject private var model = CapDepAppModel()
    private let notificationDelegate = CapDepNotificationDelegate()

    init() {
        if !Self.singleInstanceGuard.didAcquire {
            Foundation.exit(0)
        }
    }

    var body: some Scene {
        WindowGroup("CapDep", id: "main") {
            ChatView()
                .environmentObject(model)
                .task {
                    notificationDelegate.model = model
                    UNUserNotificationCenter.current().delegate = notificationDelegate
                    await model.start()
                }
        }
        .defaultSize(width: 720, height: 640)

        Window("Console", id: "console") {
            DashboardView()
                .environmentObject(model)
                .frame(minWidth: 1040, minHeight: 720)
                .task {
                    await model.start()
                }
        }

        MenuBarExtra {
            MenuBarView()
                .environmentObject(model)
                .task {
                    await model.start()
                }
        } label: {
            MenuBarLabel()
                .environmentObject(model)
        }

        Window("Approval", id: "approval-card") {
            ApprovalCardWindow()
                .environmentObject(model)
                .frame(minWidth: 640, minHeight: 520)
                .task {
                    await model.start()
                }
        }
        .windowResizability(.contentSize)

        Window("Allow Access", id: "capability-grant-card") {
            GrantCardWindow()
                .environmentObject(model)
                .frame(minWidth: 640, minHeight: 520)
                .task {
                    await model.start()
                }
        }
        .windowResizability(.contentSize)

        Window("Override", id: "override-card") {
            OverrideCardWindow()
                .environmentObject(model)
                .frame(minWidth: 640, minHeight: 560)
                .task {
                    await model.start()
                }
        }
        .windowResizability(.contentSize)

        Window("Google Account Setup", id: "google-oauth-wizard") {
            GoogleOAuthWizardView()
                .environmentObject(model)
                .task {
                    await model.start()
                }
                .onDisappear {
                    model.dismissGoogleOAuthWizard()
                }
        }
        .windowResizability(.contentSize)

        Window("Set Up CapDep", id: "onboarding-wizard") {
            OnboardingWizardView()
                .environmentObject(model)
                .task {
                    await model.start()
                }
                .onDisappear {
                    model.dismissOnboarding()
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
            Button("Focus Chat") {
                openWindow(id: "main")
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

            Button("Open Console") {
                openWindow(id: "console")
            }
            .keyboardShortcut("0", modifiers: [.command])

            Button("Open Approval Queue") {
                model.selectedSection = .approvals
                openWindow(id: "console")
            }
            .keyboardShortcut("a", modifiers: [.command, .shift])
        }

        CommandMenu("Session") {
            Button("Fork Clean Session") {
                Task {
                    await model.forkCleanSession()
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
                openWindow(id: "console")
            }
            ForEach(model.pendingApprovals.prefix(5)) { approval in
                Button("Review Approval #\(approval.id)") {
                    model.presentApproval(id: approval.id)
                    openWindow(id: "approval-card")
                }
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
                        openWindow(id: "main")
                    }
                }
            }
        }

        CommandMenu("Setup") {
            Button("First-Run Setup…") {
                model.presentOnboarding()
                openWindow(id: "onboarding-wizard")
            }
            Button("Set Up Google Account…") {
                model.presentGoogleOAuthWizard()
                openWindow(id: "google-oauth-wizard")
            }
            Button("Open Setup Assistant") {
                model.selectedSection = .setup
                openWindow(id: "console")
            }
        }

        CommandMenu("Automation") {
            Button("Pause All Automation") {
                Task {
                    await model.setAutomationPaused(true)
                }
            }
            .disabled(model.runtimeControls.automationPaused)
            Button("Resume Automation") {
                Task {
                    await model.setAutomationPaused(false)
                }
            }
            .disabled(!model.runtimeControls.automationPaused)
            Divider()
            Button("Manage App Permissions") {
                model.selectedSection = .setup
                openWindow(id: "console")
            }
            Button("Enable Screen Control for This Session") {
                Task {
                    await model.requestScreenControl()
                    model.selectedSection = .setup
                    openWindow(id: "console")
                }
            }
        }

        CommandMenu("Trust") {
            Button("Relationship Groups") {
                model.selectedSection = .trust
                openWindow(id: "console")
            }
            Button("Approval Patterns") {
                model.selectedSection = .trust
                openWindow(id: "console")
            }
            Button("Validate Configuration") {
                Task {
                    await model.validateConfiguration()
                    model.selectedSection = .setup
                    openWindow(id: "console")
                }
            }
        }

        CommandGroup(after: .help) {
            Button("Why Was This Blocked?") {
                model.selectedSection = .policyTrace
                openWindow(id: "console")
            }
        }
    }
}
