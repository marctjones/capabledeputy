import AppKit
import Foundation
import LocalAuthentication

@MainActor
final class CapDepAppModel: ObservableObject {
    @Published private(set) var connected = false
    @Published private(set) var daemonConnection = DaemonConnectionHealth.empty
    @Published private(set) var isRefreshing = false
    @Published private(set) var pendingApprovals: [Approval] = []
    @Published private(set) var sessions: [CapDepSession] = []
    @Published private(set) var events: [AuditEvent] = []
    @Published private(set) var appStatus = AppStatus.empty
    @Published private(set) var setupChecks: [SetupCheck] = []
    @Published private(set) var setupPlan = SetupPlan(dictionary: [:])
    @Published var approvalWindowID: Int?
    @Published var grantPromptPresented = false
    @Published private(set) var turnStatusLine = ""
    @Published private(set) var gmailOAuthStatus = GmailOAuthStatus.empty
    @Published private(set) var googleOAuthStatuses: [String: GoogleOAuthStatus] = [:]
    @Published private(set) var daemonSettings = DaemonSettings.empty
    @Published private(set) var imageProfiles: [ImageProfile] = []
    @Published private(set) var imageReadiness = ImageReadiness.empty
    @Published private(set) var configValidation = ConfigValidation.empty
    @Published private(set) var logLocations: [LogLocation] = []
    @Published private(set) var connectorStatuses: [ConnectorStatus] = []
    @Published private(set) var runtimeControls = RuntimeControlState.empty
    @Published private(set) var sourceBindings: [SourceBindingViewData] = []
    @Published private(set) var provenanceNodes: [ProvenanceNode] = []
    @Published private(set) var provenanceEdges: [ProvenanceEdge] = []
    @Published private(set) var relationshipGroups: [RelationshipGroupViewData] = []
    @Published private(set) var approvalPatterns: [ApprovalPatternViewData] = []
    @Published private(set) var memoryEntries: [MemoryEntryViewData] = []
    @Published private(set) var daemonTools: [DaemonToolViewData] = []
    @Published private(set) var overrideGrants: [OverrideGrantViewData] = []
    @Published private(set) var selectedSessionChildren: [CapDepSession] = []
    @Published private(set) var approvalDetails: [Int: ApprovalDetail] = [:]
    @Published private(set) var sessionSecurityContexts: [String: SessionSecurityContext] = [:]
    @Published private(set) var onguardClients: [OnguardClientViewData] = []
    @Published private(set) var onguardCommands: [OnguardCommandViewData] = []
    @Published private(set) var onguardSchedules: [OnguardScheduleViewData] = []
    @Published private(set) var onguardArtifacts: [OnguardArtifactViewData] = []
    @Published private(set) var onguardEvents: [OnguardEventViewData] = []
    @Published private(set) var onguardConfigs: [OnguardConfigViewData] = []
    @Published private(set) var onguardNotifications: [OnguardNotificationViewData] = []
    @Published private(set) var currentSessionID: String?
    @Published private(set) var chatMessages: [ChatMessage] = []
    @Published private(set) var promptRuns: [ChatPromptRun] = []
    @Published private(set) var currentToolOutcomes: [ToolOutcome] = []
    @Published private(set) var isRunningTurn = false
    @Published private(set) var currentTurnID: String?
    @Published private(set) var currentResolvedModelRole = ""
    @Published private(set) var currentResolvedModelReason = ""
    @Published private(set) var currentResolvedModelID = ""
    /// `work/images` paths authorized for the in-flight assistant stream (from tool results).
    @Published private(set) var authorizedStreamingImagePaths: Set<String> = []
    @Published private(set) var turnPendingApprovalIDs: [Int] = []
    @Published private(set) var pendingGrantRetryMessage: String?
    @Published private(set) var isRecoveringDaemon = false
    @Published private(set) var isConfiguringGoogleOAuth = false
    @Published var selectedSection: DashboardSection = .today
    @Published var selectedPurpose: Purpose = .general
    @Published var selectedChatModelMode: ChatModelMode = CapDepAppModel.storedChatModelMode() {
        didSet {
            UserDefaults.standard.set(selectedChatModelMode.rawValue, forKey: Self.chatModelModeKey)
        }
    }
    @Published var focusedApprovalID: Int?
    @Published var focusedSessionID: String?
    @Published var commandText = ""
    @Published var contextChips: [ContextChip] = []
    @Published var taskPanelPinned = false
    @Published var lastError: String?
    @Published var googleOAuthWizardServiceID: String?
    @Published var isGoogleOAuthWizardPresented = false

    let client = DaemonClient(socketPath: DaemonClient.defaultSocketPath())
    @Published private(set) var workflows: [WorkflowTemplate] = []
    private let notifications = NotificationCenterBridge()
    private let daemonSupervisor = DaemonSupervisor()
    private var didStart = false
    private var isProcessingPromptQueue = false
    private var didStartGuiTestCommandHook = false
    private var guiTestCommandLineCount = 0
    private var lastPendingApprovalCount = 0
    private var lastNotifiedApprovalID: Int?
    private var streamingAssistantMessageID: String?
    private static let chatModelModeKey = "CapDep.chatModelMode"
    private static let promptRunsStorageKey = "CapDep.pendingPromptRuns.v1"
    private static let requiredDaemonMethods = [
        "app.status",
        "approval.list",
        "audit.tail",
        "daemon.methods",
        "image.profile.get",
        "image.profile.set",
        "image.profiles",
        "image.readiness",
        "ping",
        "session.get",
        "session.list",
        "session.new",
        "session.turn.ack",
        "session.turn.events",
        "session.turn.get",
        "session.turn.start",
        "version",
    ]


    var currentAssistantOutput: String {
        chatMessages.last(where: { $0.role == .assistant })?.content ?? ""
    }

    var queuedPromptRuns: [ChatPromptRun] {
        promptRuns.filter { $0.status == .queued }
    }

    var activePromptRun: ChatPromptRun? {
        promptRuns.last { $0.status == .running }
    }

    /// Changes when streaming text grows so chat can follow newly rendered blocks.
    var chatScrollAnchor: String {
        guard let last = chatMessages.last else {
            return ""
        }
        return "\(last.id)-\(last.content.count)-\(last.isStreaming)"
    }

    init() {
        restorePendingPromptRuns()
        Task {
            await start()
        }
    }

    private static func storedChatModelMode() -> ChatModelMode {
        let raw = UserDefaults.standard.string(forKey: chatModelModeKey) ?? ""
        return ChatModelMode(rawValue: raw) ?? .automatic
    }

    private func setDaemonConnection(
        _ phase: DaemonConnectionPhase,
        version: String? = nil,
        missingMethods: [String] = [],
        detail: String = "",
    ) {
        daemonConnection = DaemonConnectionHealth(
            phase: phase,
            version: version ?? daemonConnection.version,
            socketPath: client.socketPath,
            missingMethods: missingMethods,
            detail: detail,
        )
        connected = daemonConnection.isUsable
    }

    private func restorePendingPromptRuns() {
        guard let data = UserDefaults.standard.data(forKey: Self.promptRunsStorageKey) else {
            return
        }
        guard let restored = try? JSONDecoder().decode([ChatPromptRun].self, from: data) else {
            UserDefaults.standard.removeObject(forKey: Self.promptRunsStorageKey)
            return
        }
        promptRuns = restored
            .filter { !$0.isTerminal }
            .map { run in
                var recovered = run
                recovered.status = .failed
                recovered.error = "Recovered after app restart. Please resend if this turn did not finish."
                return recovered
            }
        persistPendingPromptRuns()
    }

    private func persistPendingPromptRuns() {
        let pending = promptRuns.filter { !$0.isTerminal }
        guard !pending.isEmpty else {
            UserDefaults.standard.removeObject(forKey: Self.promptRunsStorageKey)
            return
        }
        if let data = try? JSONEncoder().encode(pending) {
            UserDefaults.standard.set(data, forKey: Self.promptRunsStorageKey)
        }
    }

    @discardableResult
    private func verifyDaemonHandshake() async -> Bool {
        do {
            _ = try await client.call(method: "ping")
            let versionResult = try await client.call(method: "version") as? [String: Any]
            let version = versionResult?["version"] as? String ?? ""
            let methodsResult = try await client.call(method: "daemon.methods") as? [String: Any]
            let methods = Set(methodsResult?["methods"] as? [String] ?? [])
            let missing = DaemonConnectionHealth.missingRequiredMethods(
                available: methods,
                required: Self.requiredDaemonMethods,
            )
            guard missing.isEmpty else {
                setDaemonConnection(
                    .incompatible,
                    version: version,
                    missingMethods: missing,
                    detail: "Daemon is missing required RPCs: \(missing.joined(separator: ", "))",
                )
                lastError = daemonConnection.detail
                return false
            }
            setDaemonConnection(.connected, version: version)
            lastError = nil
            return true
        } catch {
            setDaemonConnection(.unhealthy, detail: error.localizedDescription)
            lastError = error.localizedDescription
            return false
        }
    }

    func start() async {
        guard !didStart else {
            return
        }
        didStart = true
        setDaemonConnection(.starting)
        await notifications.requestAuthorizationIfNeeded()
        await ensureDaemonRunning()
        await refresh()
        seedDemoImageIfNeeded()
        startGuiTestCommandHookIfNeeded()
        Task {
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(5))
                await refresh()
            }
        }
    }

    private func startGuiTestCommandHookIfNeeded() {
        guard !didStartGuiTestCommandHook else {
            return
        }
        guard let commandPath = ProcessInfo.processInfo.environment["CAPDEP_GUI_TEST_COMMAND_FILE"],
              !commandPath.isEmpty else {
            return
        }
        didStartGuiTestCommandHook = true
        ChatDebugLog.log("gui_test_hook_started", metadata: ["command_file": commandPath])
        Task {
            while !Task.isCancelled {
                await processGuiTestCommands(commandPath: commandPath)
                try? await Task.sleep(for: .milliseconds(500))
            }
        }
    }

    private func processGuiTestCommands(commandPath: String) async {
        guard let text = try? String(contentsOfFile: commandPath, encoding: .utf8) else {
            return
        }
        let lines = text.split(whereSeparator: \.isNewline).map(String.init)
        guard lines.count > guiTestCommandLineCount else {
            return
        }
        for line in lines.dropFirst(guiTestCommandLineCount) {
            await handleGuiTestCommand(line)
        }
        guiTestCommandLineCount = lines.count
    }

    private func handleGuiTestCommand(_ line: String) async {
        guard let data = line.data(using: .utf8),
              let raw = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            ChatDebugLog.log("gui_test_hook_invalid_command")
            return
        }
        let command = raw["command"] as? String ?? ""
        switch command {
        case "submit_prompt":
            let message = raw["message"] as? String ?? ""
            guard !message.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                ChatDebugLog.log("gui_test_hook_empty_prompt")
                return
            }
            commandText = message
            ChatDebugLog.log(
                "gui_test_hook_submit_prompt",
                metadata: ["message_preview": String(message.prefix(200))],
            )
            await submitCommand()
        case "queue_prompt":
            let message = raw["message"] as? String ?? ""
            let trimmed = message.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !trimmed.isEmpty else {
                ChatDebugLog.log("gui_test_hook_empty_prompt")
                return
            }
            ChatDebugLog.log(
                "gui_test_hook_queue_prompt",
                metadata: ["message_preview": String(trimmed.prefix(200))],
            )
            appendPromptRun(message: trimmed, purpose: selectedPurpose)
            Task {
                await processPromptQueue()
            }
        default:
            ChatDebugLog.log("gui_test_hook_unknown_command", metadata: ["command": command])
        }
    }

    private func seedDemoImageIfNeeded() {
        guard chatMessages.isEmpty else {
            return
        }
        guard let imagePath = ProcessInfo.processInfo.environment["CAPDEP_DEMO_IMAGE"],
              !imagePath.isEmpty,
              FileManager.default.fileExists(atPath: imagePath) else {
            return
        }
        chatMessages = [
            ChatMessage(
                role: .user,
                content: "Show me the demo image inline.",
            ),
            ChatMessage(
                role: .assistant,
                content: """
                Here is the rendered picture:

                ![Cartoon cat](\(imagePath))
                """.trimmingCharacters(in: .whitespacesAndNewlines),
            ),
        ]
    }

    func refresh() async {
        guard !isRefreshing else {
            return
        }
        isRefreshing = true
        defer {
            isRefreshing = false
        }

        do {
            guard await verifyDaemonHandshake() else {
                if daemonConnection.phase == .unhealthy, !isRecoveringDaemon {
                    Task {
                        await ensureDaemonRunning()
                    }
                }
                return
            }
            async let approvalsResult = client.call(
                method: "approval.list",
                params: ["status": "pending"],
            )
            async let sessionsResult = client.call(method: "session.list")
            async let auditResult = client.call(method: "audit.tail", params: ["limit": 40])
            async let statusResult = client.call(method: "app.status")
            async let setupResult = client.call(method: "setup.plan")
            async let workflowsResult = client.call(method: "workflow.templates")
            async let googleOAuthResult = client.call(method: "setup.google.oauth_status")
            async let settingsResult = client.call(method: "settings.get")
            async let imageProfilesResult = client.call(method: "image.profiles")
            async let imageReadinessResult = client.call(method: "image.readiness")
            async let validationResult = client.call(method: "config.validate")
            async let logLocationsResult = client.call(method: "config.log_locations")
            async let connectorsResult = client.call(method: "connector.status")
            async let runtimeResult = client.call(method: "runtime.status")
            async let sourceBindingsResult = client.call(method: "source_binding.list")
            async let provenanceResult = client.call(method: "provenance.graph")
            async let relationshipResult = client.call(method: "relationship_group.list")
            async let patternsResult = client.call(method: "approval_pattern.list")
            async let memoryResult = client.call(method: "memory.entries")
            async let toolsResult = client.call(method: "tool.list")
            async let overridesResult = client.call(method: "override.list")
            async let onguardClientsResult = client.call(
                method: "client.registry.list",
                params: ["kind": "onguard"],
            )
            async let onguardCommandsResult = client.call(method: "client.queue.list")
            async let onguardSchedulesResult = client.call(method: "schedule.list")
            async let onguardArtifactsResult = client.call(method: "artifact.list")
            async let onguardEventsResult = client.call(
                method: "client.events.list",
                params: ["limit": 100],
            )
            async let onguardConfigsResult = client.call(method: "client.config.list")
            async let onguardNotificationsResult = client.call(
                method: "onguard.notifications.list",
                params: ["limit": 50],
            )

            let approvalsObject = try await approvalsResult as? [String: Any]
            let sessionsObject = try await sessionsResult as? [String: Any]
            let auditObject = try await auditResult as? [String: Any]
            let statusObject = try await statusResult as? [String: Any]
            let setupObject = try await setupResult as? [String: Any]
            let workflowsObject = try await workflowsResult as? [String: Any]
            let googleOAuthObject = try await googleOAuthResult as? [String: Any]
            let settingsObject = try await settingsResult as? [String: Any]
            let imageProfilesObject = try await imageProfilesResult as? [String: Any]
            let imageReadinessObject = try await imageReadinessResult as? [String: Any]
            let validationObject = try await validationResult as? [String: Any]
            let logLocationsObject = try await logLocationsResult as? [String: Any]
            let connectorsObject = try await connectorsResult as? [String: Any]
            let runtimeObject = try await runtimeResult as? [String: Any]
            let sourceBindingsObject = try await sourceBindingsResult as? [String: Any]
            let provenanceObject = try await provenanceResult as? [String: Any]
            let relationshipObject = try await relationshipResult as? [String: Any]
            let patternsObject = try await patternsResult as? [String: Any]
            let memoryObject = try await memoryResult as? [String: Any]
            let toolsObject = try await toolsResult as? [String: Any]
            let overridesObject = try await overridesResult as? [String: Any]
            let onguardClientsObject = try await onguardClientsResult as? [String: Any]
            let onguardCommandsObject = try await onguardCommandsResult as? [String: Any]
            let onguardSchedulesObject = try await onguardSchedulesResult as? [String: Any]
            let onguardArtifactsObject = try await onguardArtifactsResult as? [String: Any]
            let onguardEventsObject = try await onguardEventsResult as? [String: Any]
            let onguardConfigsObject = try await onguardConfigsResult as? [String: Any]
            let onguardNotificationsObject = try await onguardNotificationsResult as? [String: Any]

            let approvals = (approvalsObject?["approvals"] as? [[String: Any]] ?? [])
                .map(Approval.init(dictionary:))
                .sorted { $0.id < $1.id }
            pendingApprovals = approvals
            sessions = (sessionsObject?["sessions"] as? [[String: Any]] ?? [])
                .map(CapDepSession.init(dictionary:))
                .sorted { $0.id < $1.id }
            events = (auditObject?["events"] as? [[String: Any]] ?? [])
                .map(AuditEvent.init(dictionary:))
                .reversed()
            appStatus = AppStatus(dictionary: statusObject ?? [:])
            setupPlan = SetupPlan(dictionary: setupObject ?? [:])
            setupChecks = (setupObject?["checks"] as? [[String: Any]] ?? [])
                .map(SetupCheck.init(dictionary:))
            workflows = (workflowsObject?["templates"] as? [[String: Any]] ?? [])
                .map(WorkflowTemplate.init(dictionary:))
            let googleStatuses = (
                googleOAuthObject?["services"] as? [[String: Any]] ?? []
            ).map(GoogleOAuthStatus.init(dictionary:))
            googleOAuthStatuses = Dictionary(
                uniqueKeysWithValues: googleStatuses.map { ($0.serviceID, $0) },
            )
            gmailOAuthStatus = googleOAuthStatuses["google-gmail"] ?? GmailOAuthStatus.empty
            daemonSettings = DaemonSettings(
                dictionary: settingsObject?["settings"] as? [String: Any] ?? [:],
            )
            imageProfiles = (imageProfilesObject?["profiles"] as? [[String: Any]] ?? [])
                .map(ImageProfile.init(dictionary:))
            imageReadiness = ImageReadiness(dictionary: imageReadinessObject ?? [:])
            configValidation = ConfigValidation(dictionary: validationObject ?? [:])
            logLocations = (
                (logLocationsObject?["logs"] as? [[String: Any]] ?? [])
                    + (logLocationsObject?["directories"] as? [[String: Any]] ?? [])
            )
            .map(LogLocation.init(dictionary:))
            connectorStatuses = (connectorsObject?["connectors"] as? [[String: Any]] ?? [])
                .map(ConnectorStatus.init(dictionary:))
            runtimeControls = RuntimeControlState(
                dictionary: runtimeObject?["runtime"] as? [String: Any] ?? [:],
            )
            sourceBindings = (sourceBindingsObject?["bindings"] as? [[String: Any]] ?? [])
                .map(SourceBindingViewData.init(dictionary:))
            provenanceNodes = (provenanceObject?["nodes"] as? [[String: Any]] ?? [])
                .map(ProvenanceNode.init(dictionary:))
            provenanceEdges = (provenanceObject?["edges"] as? [[String: Any]] ?? [])
                .map(ProvenanceEdge.init(dictionary:))
            relationshipGroups = (relationshipObject?["groups"] as? [[String: Any]] ?? [])
                .map(RelationshipGroupViewData.init(dictionary:))
            approvalPatterns = (patternsObject?["patterns"] as? [[String: Any]] ?? [])
                .map(ApprovalPatternViewData.init(dictionary:))
            memoryEntries = (memoryObject?["entries"] as? [[String: Any]] ?? [])
                .map(MemoryEntryViewData.init(dictionary:))
            daemonTools = (toolsObject?["tools"] as? [[String: Any]] ?? [])
                .map(DaemonToolViewData.init(dictionary:))
            overrideGrants = (overridesObject?["grants"] as? [[String: Any]] ?? [])
                .map(OverrideGrantViewData.init(dictionary:))
            onguardClients = (onguardClientsObject?["clients"] as? [[String: Any]] ?? [])
                .map(OnguardClientViewData.init(dictionary:))
            onguardCommands = (onguardCommandsObject?["commands"] as? [[String: Any]] ?? [])
                .map(OnguardCommandViewData.init(dictionary:))
            onguardSchedules = (onguardSchedulesObject?["schedules"] as? [[String: Any]] ?? [])
                .map(OnguardScheduleViewData.init(dictionary:))
            onguardArtifacts = (onguardArtifactsObject?["artifacts"] as? [[String: Any]] ?? [])
                .map(OnguardArtifactViewData.init(dictionary:))
            onguardEvents = (onguardEventsObject?["events"] as? [[String: Any]] ?? [])
                .map(OnguardEventViewData.init(dictionary:))
            onguardConfigs = (onguardConfigsObject?["configs"] as? [[String: Any]] ?? [])
                .map(OnguardConfigViewData.init(dictionary:))
            let daemonNotifications = (
                onguardNotificationsObject?["notifications"] as? [[String: Any]] ?? []
            )
            .map(OnguardNotificationViewData.init(dictionary:))
            onguardNotifications = daemonNotifications
            setDaemonConnection(.connected, version: appStatus.version)
            lastError = nil
            for notification in daemonNotifications where notification.urgency == "high" {
                await notifications.notifyOnguard(notification)
            }
            if approvals.count > lastPendingApprovalCount, let first = approvals.first {
                await notifications.notifyPendingApproval(
                    count: approvals.count,
                    approvalID: first.id,
                )
                lastNotifiedApprovalID = first.id
            }
            lastPendingApprovalCount = approvals.count
        } catch {
            setDaemonConnection(.disconnected, detail: error.localizedDescription)
            lastError = error.localizedDescription
            if shouldRecoverDaemon(after: error) {
                Task {
                    setDaemonConnection(.reconnecting, detail: error.localizedDescription)
                    await ensureDaemonRunning()
                    await refresh()
                }
            }
        }
    }

    func ensureDaemonRunning() async {
        guard !isRecoveringDaemon else {
            return
        }
        isRecoveringDaemon = true
        setDaemonConnection(.reconnecting)
        defer {
            isRecoveringDaemon = false
        }
        do {
            try await daemonSupervisor.ensureRunning(client: client)
            _ = await verifyDaemonHandshake()
        } catch {
            setDaemonConnection(.disconnected, detail: error.localizedDescription)
            lastError = error.localizedDescription
        }
    }

    func approve(_ approval: Approval) async {
        await decide(approval, approved: true)
    }

    func focusApproval(_ approval: Approval) {
        focusedApprovalID = approval.id
        approvalWindowID = approval.id
        selectedSection = .approvals
    }

    func presentApproval(id: Int) {
        focusedApprovalID = id
        approvalWindowID = id
        selectedSection = .approvals
    }

    func focusSession(_ session: CapDepSession) {
        currentSessionID = session.id
        focusedSessionID = session.id
        selectedSection = .sessions
        Task {
            await loadChatHistory(sessionID: session.id)
            await refreshSecurityContext(sessionID: session.id)
        }
    }

    func loadChatHistory(sessionID: String) async {
        do {
            let result = try await client.call(
                method: "session.get",
                params: ["session_id": sessionID],
            ) as? [String: Any]
            let history = result?["history"] as? [[String: Any]] ?? []
            chatMessages = ChatMessage.fromHistory(history)
            streamingAssistantMessageID = nil
            authorizedStreamingImagePaths = []
            promptRuns.removeAll { $0.isTerminal }
            persistPendingPromptRuns()
            if chatMessages.isEmpty {
                seedDemoImageIfNeeded()
            }
        } catch {
            lastError = error.localizedDescription
        }
    }

    private func beginTurn(userMessage: String) {
        authorizedStreamingImagePaths = []
        currentResolvedModelRole = ""
        currentResolvedModelReason = ""
        currentResolvedModelID = ""
        chatMessages.append(ChatMessage(role: .user, content: userMessage))
        let assistant = ChatMessage(role: .assistant, content: "", isStreaming: true)
        chatMessages.append(assistant)
        streamingAssistantMessageID = assistant.id
    }

    private func isImageGenerateToolName(_ name: String) -> Bool {
        name == "image.generate"
            || name == "bundled-image-generate.image.generate"
            || name == "bundled-images.image.generate"
    }

    private func authorizeStreamingImagePath(_ path: String) {
        let expanded = NSString(string: path).expandingTildeInPath
        guard ChatMarkdownParser.isGeneratedWorkImagePath(expanded) else {
            return
        }
        authorizedStreamingImagePaths.insert(expanded)
    }

    private func authorizeStreamingImagePaths(fromToolOutcome outcome: [String: Any]) {
        guard outcome["decision"] as? String == "allow" else {
            return
        }
        let toolName = outcome["tool_name"] as? String ?? ""
        guard isImageGenerateToolName(toolName) else {
            return
        }
        guard let output = outcome["output"] as? [String: Any] else {
            return
        }
        if let imagePath = output["image_path"] as? String {
            authorizeStreamingImagePath(imagePath)
        }
        if let markdown = output["markdown"] as? String {
            for path in ChatMarkdownParser.extractMarkdownImagePaths(from: markdown) {
                authorizeStreamingImagePath(path)
            }
        }
    }

    private func stripUnverifiedGeneratedImagesFromStreamingAssistant() {
        guard
            let messageID = streamingAssistantMessageID,
            let index = chatMessages.firstIndex(where: { $0.id == messageID })
        else {
            return
        }
        let current = chatMessages[index].content
        let cleaned = ChatMarkdownParser.stripUnverifiedGeneratedImageMarkdown(
            from: current,
            authorizedImagePaths: authorizedStreamingImagePaths,
        )
        guard cleaned != current else {
            return
        }
        var message = chatMessages[index]
        message.content = cleaned
        chatMessages[index] = message
    }

    private func setStreamingAssistantContent(_ content: String) {
        guard
            let messageID = streamingAssistantMessageID,
            let index = chatMessages.firstIndex(where: { $0.id == messageID })
        else {
            return
        }
        var message = chatMessages[index]
        message.content = content
        chatMessages[index] = message
    }

    func appendImageAttachmentToStreamingAssistant(alt: String, path: String) {
        guard let snippet = ChatImageAttachment.markdownSnippet(alt: alt, path: path) else {
            return
        }
        let current = chatMessages.last(where: { $0.id == streamingAssistantMessageID })?.content ?? ""
        guard let merged = ChatImageAttachment.appendSnippet(snippet, to: current) else {
            return
        }
        setStreamingAssistantContent(merged)
    }

    private func resolvedAssistantContent(
        partial: String?,
        fallback: String = "",
    ) -> String {
        if let partial, !partial.isEmpty {
            return partial
        }
        let streamed = chatMessages.last(where: { $0.id == streamingAssistantMessageID })?.content ?? ""
        if !streamed.isEmpty {
            return streamed
        }
        return fallback
    }

    private func finalizeStreamingAssistant(_ content: String) {
        guard
            let messageID = streamingAssistantMessageID,
            let index = chatMessages.firstIndex(where: { $0.id == messageID })
        else {
            return
        }
        var message = chatMessages[index]
        message.content = ChatContentFormatter.displayText(content)
        message.isStreaming = false
        chatMessages[index] = message
        streamingAssistantMessageID = nil
        authorizedStreamingImagePaths = []
    }

    func deny(_ approval: Approval) async {
        await decide(approval, approved: false)
    }

    @discardableResult
    func createSession(intent: String, purpose: Purpose? = nil) async -> CapDepSession? {
        let chosenPurpose = purpose ?? selectedPurpose
        do {
            let result = try await client.call(
                method: "session.new",
                params: [
                    "intent": intent,
                    "owner": "CapDepMac",
                    "purpose_handle": chosenPurpose.rawValue,
                ],
            ) as? [String: Any]
            let session = CapDepSession(dictionary: result ?? [:])
            currentSessionID = session.id
            selectedSection = .sessions
            await loadChatHistory(sessionID: session.id)
            await refresh()
            return session
        } catch {
            lastError = error.localizedDescription
            return nil
        }
    }

    func launchWorkflow(_ workflow: WorkflowTemplate) async {
        do {
            selectedPurpose = workflow.purpose
            let result = try await client.call(
                method: "workflow.launch",
                params: [
                    "template_id": workflow.id,
                    "client_id": "capdep-mac",
                ],
            ) as? [String: Any]
            let session = CapDepSession(dictionary: result?["session"] as? [String: Any] ?? [:])
            if !session.id.isEmpty {
                currentSessionID = session.id
                selectedSection = .sessions
                await loadChatHistory(sessionID: session.id)
            }
            await refresh()
        } catch {
            lastError = error.localizedDescription
        }
    }

    func submitCommand(purpose: Purpose? = nil, forceNewSession: Bool = false) async {
        let trimmed = commandText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            return
        }
        commandText = ""
        let chosenPurpose = purpose ?? selectedPurpose
        appendPromptRun(message: trimmed, purpose: chosenPurpose)
        await processPromptQueue(forceNewFirstSession: forceNewSession)
    }

    @discardableResult
    private func appendPromptRun(message trimmed: String, purpose chosenPurpose: Purpose) -> ChatPromptRun {
        let daemonMessage = selectedChatModelMode.daemonMessage(for: trimmed)
        let run = ChatPromptRun(
            displayMessage: trimmed,
            daemonMessage: daemonMessage,
            purpose: chosenPurpose,
        )
        promptRuns.append(run)
        persistPendingPromptRuns()
        ChatDebugLog.log(
            "submit_queued",
            metadata: [
                "prompt_id": run.id,
                "purpose": chosenPurpose.rawValue,
                "model_mode": selectedChatModelMode.rawValue,
                "message_len": String(trimmed.count),
                "message_preview": String(trimmed.prefix(200)),
            ],
        )
        return run
    }

    private func processPromptQueue(forceNewFirstSession: Bool = false) async {
        guard !isProcessingPromptQueue else {
            return
        }
        guard daemonConnection.isUsable else {
            lastError = daemonConnection.detail.isEmpty
                ? "Daemon is not ready for chat turns."
                : daemonConnection.detail
            persistPendingPromptRuns()
            return
        }
        isProcessingPromptQueue = true
        defer {
            isProcessingPromptQueue = false
        }

        var forceNew = forceNewFirstSession
        while let index = promptRuns.firstIndex(where: { $0.status == .queued }) {
            var run = promptRuns[index]
            markPromptRun(run.id, status: .running)
            guard let session = await ensureSession(
                intent: run.displayMessage,
                purpose: run.purpose,
                forceNew: forceNew,
            ) else {
                markPromptRun(run.id, status: .failed, error: "Could not create or reuse a session.")
                forceNew = false
                continue
            }
            forceNew = false
            run.sessionID = session.id
            updatePromptRun(run)
            ChatDebugLog.log(
                "session_ready",
                metadata: [
                    "prompt_id": run.id,
                    "session_id": session.id,
                    "purpose": session.purpose,
                ],
            )
            let succeeded = await send(
                message: run.daemonMessage,
                displayMessage: run.displayMessage,
                sessionID: session.id,
                promptID: run.id,
            )
            if succeeded {
                markPromptRun(run.id, status: .completed)
            } else if promptRuns.first(where: { $0.id == run.id })?.status == .running {
                markPromptRun(run.id, status: .failed, error: lastError ?? "Turn failed.")
            }
        }
    }

    private func updatePromptRun(_ run: ChatPromptRun) {
        guard let index = promptRuns.firstIndex(where: { $0.id == run.id }) else {
            return
        }
        promptRuns[index] = run
        persistPendingPromptRuns()
    }

    private func markPromptRun(
        _ id: String,
        status: ChatPromptStatus,
        turnID: String? = nil,
        error: String? = nil,
    ) {
        guard let index = promptRuns.firstIndex(where: { $0.id == id }) else {
            return
        }
        var run = promptRuns[index]
        run.status = status
        if let turnID {
            run.turnID = turnID
        }
        run.error = error
        promptRuns[index] = run
        persistPendingPromptRuns()
    }

    func ensureSession(
        intent: String,
        purpose: Purpose,
        forceNew: Bool = false,
    ) async -> CapDepSession? {
        if !forceNew, let currentSessionID,
           let existing = sessions.first(where: { $0.id == currentSessionID }),
           existing.status == "active",
           (existing.purpose.isEmpty || existing.purpose == purpose.rawValue) {
            return existing
        }
        return await createSession(intent: intent, purpose: purpose)
    }

    func forkCleanSession() async -> CapDepSession? {
        guard let parentID = currentSessionID ?? sessions.first(where: { $0.status == "active" })?.id else {
            return await createSession(intent: "Clean recovery session")
        }
        do {
            let result = try await client.call(
                method: "session.fork",
                params: [
                    "parent_id": parentID,
                    "intent": "Clean recovery session",
                ],
            ) as? [String: Any]
            let session = CapDepSession(dictionary: result ?? [:])
            currentSessionID = session.id
            selectedSection = .sessions
            await loadChatHistory(sessionID: session.id)
            await refresh()
            return session
        } catch {
            lastError = error.localizedDescription
            return nil
        }
    }

    @discardableResult
    func send(
        message: String,
        displayMessage: String? = nil,
        sessionID: String,
        promptID: String? = nil,
    ) async -> Bool {
        let visibleMessage = displayMessage ?? message
        pendingGrantRetryMessage = nil
        grantPromptPresented = false
        if let demoResponse = localDemoImageResponse(for: visibleMessage) {
            ChatDebugLog.log(
                "local_demo_image_shortcut",
                metadata: [
                    "session_id": sessionID,
                    "path": ProcessInfo.processInfo.environment["CAPDEP_DEMO_IMAGE"] ?? "",
                ],
            )
            beginTurn(userMessage: visibleMessage)
            for path in ChatMarkdownParser.extractMarkdownImagePaths(from: demoResponse) {
                authorizeStreamingImagePath(path)
            }
            finalizeStreamingAssistant(demoResponse)
            return true
        }
        return await runTurn(
            message: message,
            displayMessage: visibleMessage,
            sessionID: sessionID,
            appendUserMessage: true,
            promptID: promptID,
        )
    }

    private func localDemoImageResponse(for message: String) -> String? {
        guard let imagePath = ProcessInfo.processInfo.environment["CAPDEP_DEMO_IMAGE"],
              !imagePath.isEmpty,
              FileManager.default.fileExists(atPath: imagePath) else {
            return nil
        }
        let lower = message.lowercased()
        guard lower.contains("demo"),
              lower.contains("cat") || lower.contains("image") else {
            return nil
        }
        return """
        Here is the demo cat inline:

        ![Cartoon cat](\(imagePath))
        """.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    @discardableResult
    private func runTurn(
        message: String,
        displayMessage: String? = nil,
        sessionID: String,
        appendUserMessage: Bool,
        promptID: String? = nil,
    ) async -> Bool {
        isRunningTurn = true
        if appendUserMessage {
            beginTurn(userMessage: displayMessage ?? message)
        } else {
            let assistant = ChatMessage(role: .assistant, content: "", isStreaming: true)
            chatMessages.append(assistant)
            streamingAssistantMessageID = assistant.id
        }
        currentToolOutcomes = []
        turnPendingApprovalIDs = []
        turnStatusLine = "Starting turn…"
        currentTurnID = nil
        var observedTurnID = ""
        ChatDebugLog.log(
            "turn_send_start",
            metadata: [
                "session_id": sessionID,
                "message_len": String(message.count),
                "append_user_message": String(appendUserMessage),
            ],
        )
        defer {
            isRunningTurn = false
            turnStatusLine = ""
            currentTurnID = nil
            noteGrantRetryIfNeeded()
            presentPolicyPromptsAfterTurn()
            Task {
                await processPromptQueue()
            }
            ChatDebugLog.log(
                "turn_send_end",
                metadata: [
                    "session_id": sessionID,
                    "turn_id": observedTurnID,
                    "output_len": String(currentAssistantOutput.count),
                    "output_has_image_markdown": String(
                        currentAssistantOutput.contains("![") && currentAssistantOutput.contains("](")
                    ),
                    "output_preview": String(currentAssistantOutput.prefix(200)),
                ],
            )
        }
        do {
            let start = try await client.call(
                method: "session.turn.start",
                params: [
                    "session_id": sessionID,
                    "message": message,
                    "client_id": "CapDepMac",
                    "heartbeat_timeout_seconds": 300,
                ],
            ) as? [String: Any]
            let turn = start?["turn"] as? [String: Any] ?? [:]
            let turnID = turn["id"] as? String ?? ""
            guard !turnID.isEmpty else {
                throw NSError(
                    domain: "CapDepMac",
                    code: 1,
                    userInfo: [NSLocalizedDescriptionKey: "Daemon did not return a turn id."],
                )
            }
            observedTurnID = turnID
            currentTurnID = turnID
            if let promptID {
                markPromptRun(promptID, status: .running, turnID: turnID)
            }
            currentSessionID = sessionID
            let turnStream = turn["stream"] as? String ?? "turn:\(turnID)"
            ChatDebugLog.log(
                "turn_started",
                metadata: [
                    "turn_id": turnID,
                    "session_id": sessionID,
                    "stream": turnStream,
                ],
            )

            var finished = false
            let heartbeatTask = Task {
                while !Task.isCancelled {
                    try? await Task.sleep(for: .seconds(10))
                    guard !Task.isCancelled else { return }
                    _ = try? await client.call(
                        method: "session.turn.ack",
                        params: [
                            "turn_id": turnID,
                            "client_id": "CapDepMac",
                        ],
                    )
                }
            }
            defer { heartbeatTask.cancel() }
            do {
                let subscription = client.subscribe(
                    streams: [turnStream],
                    cancelTurnsOnDisconnect: [turnID],
                )
                for try await envelopeData in subscription {
                    guard
                        let envelope = try JSONSerialization.jsonObject(with: envelopeData) as? [String: Any]
                    else {
                        continue
                    }
                    let event = envelope["data"] as? [String: Any] ?? [:]
                    guard (event["turn_id"] as? String) == turnID else {
                        continue
                    }
                    if try applyTurnEvent(event) {
                        finished = true
                        break
                    }
                }
            } catch {
                ChatDebugLog.log(
                    "subscribe_error",
                    metadata: [
                        "turn_id": turnID,
                        "error": error.localizedDescription,
                    ],
                )
            }
            if !finished {
                ChatDebugLog.log("subscribe_fallback_poll", metadata: ["turn_id": turnID])
                try await pollTurnUntilFinished(turnID: turnID)
            }
            return true
        } catch {
            lastError = error.localizedDescription
            if let promptID {
                markPromptRun(promptID, status: .failed, error: error.localizedDescription)
            }
            if streamingAssistantMessageID != nil {
                finalizeStreamingAssistant("[turn failed: \(error.localizedDescription)]")
            }
            ChatDebugLog.log(
                "turn_error",
                metadata: [
                    "session_id": sessionID,
                    "turn_id": observedTurnID,
                    "error": error.localizedDescription,
                ],
            )
            return false
        }
    }

    private func collectTurnApprovalIDs(from outcomes: [ToolOutcome]) {
        for outcome in outcomes where outcome.decision == "require_approval" {
            if let approvalID = outcome.approvalID, !turnPendingApprovalIDs.contains(approvalID) {
                turnPendingApprovalIDs.append(approvalID)
                presentApprovalPromptIfNeeded(approvalID: approvalID)
            }
        }
    }

    private func noteTurnApprovalRequest(from event: [String: Any]) {
        guard event["type"] as? String == "tool_returned" else { return }
        let payload = event["payload"] as? [String: Any] ?? [:]
        let outcome = payload["outcome"] as? [String: Any] ?? [:]
        guard outcome["decision"] as? String == "require_approval" else { return }
        if let rawID = outcome["approval_id"] as? Int {
            if !turnPendingApprovalIDs.contains(rawID) {
                turnPendingApprovalIDs.append(rawID)
                presentApprovalPromptIfNeeded(approvalID: rawID)
            }
        } else if let rawID = outcome["approval_id"] as? NSNumber {
            let approvalID = rawID.intValue
            if !turnPendingApprovalIDs.contains(approvalID) {
                turnPendingApprovalIDs.append(approvalID)
                presentApprovalPromptIfNeeded(approvalID: approvalID)
            }
        }
    }

    private func applyTurnEvent(_ event: [String: Any]) throws -> Bool {
        guard let eventTurnID = event["turn_id"] as? String, !eventTurnID.isEmpty else {
            ChatDebugLog.log("ignored_turn_event", metadata: ["reason": "missing turn_id"])
            return false
        }
        guard eventTurnID == currentTurnID else {
            ChatDebugLog.log(
                "ignored_turn_event",
                metadata: [
                    "reason": "turn_id mismatch",
                    "event_turn_id": eventTurnID,
                    "current_turn_id": currentTurnID ?? "",
                ],
            )
            return false
        }
        turnStatusLine = describeTurnEvent(event)
        let type = event["type"] as? String ?? ""
        let payload = event["payload"] as? [String: Any] ?? [:]
        if type == "llm_response_received" {
            let toolCalls = payload["n_tool_calls"] as? Int ?? 0
            if toolCalls > 0 {
                setStreamingAssistantContent("")
            }
        } else if type == "model_selected" {
            currentResolvedModelRole = payload["role"] as? String ?? ""
            currentResolvedModelReason = payload["reason"] as? String ?? ""
            currentResolvedModelID = payload["model"] as? String ?? ""
        } else if type == "llm_token" {
            if let partial = payload["partial_content"] as? String, !partial.isEmpty {
                setStreamingAssistantContent(partial)
            } else if let text = payload["text"] as? String {
                let current = chatMessages.last(where: { $0.id == streamingAssistantMessageID })?.content ?? ""
                setStreamingAssistantContent(current + text)
            }
            let streamed = chatMessages.last(where: { $0.id == streamingAssistantMessageID })?.content ?? ""
            ChatDebugLog.log(
                "llm_token",
                metadata: [
                    "turn_id": currentTurnID ?? "",
                    "token": payload["text"] as? String ?? "",
                    "partial_len": String(streamed.count),
                    "partial_tail": String(streamed.suffix(80)),
                ],
            )
        } else if type == "tool_dispatched" {
            let toolName = payload["tool_name"] as? String ?? ""
            if isImageGenerateToolName(toolName) {
                stripUnverifiedGeneratedImagesFromStreamingAssistant()
            }
        } else if type == "tool_returned" {
            let outcome = payload["outcome"] as? [String: Any] ?? [:]
            authorizeStreamingImagePaths(fromToolOutcome: outcome)
        } else if type == "image_attachment" {
            let path = payload["path"] as? String ?? ""
            let alt = payload["alt"] as? String ?? "image"
            authorizeStreamingImagePath(path)
            ChatDebugLog.log(
                "image_attachment",
                metadata: [
                    "turn_id": currentTurnID ?? "",
                    "path": path,
                    "alt": alt,
                ],
            )
            appendImageAttachmentToStreamingAssistant(alt: alt, path: path)
        } else {
            ChatDebugLog.log(
                "turn_event",
                metadata: [
                    "turn_id": currentTurnID ?? "",
                    "type": type,
                    "status": turnStatusLine,
                ],
            )
        }
        noteTurnApprovalRequest(from: event)
        switch type {
        case "completed":
            let result = payload["result"] as? [String: Any] ?? [:]
            let streamed = chatMessages.last(where: { $0.id == streamingAssistantMessageID })?.content ?? ""
            let finalText = result["content"] as? String
                ?? chatMessages.last(where: { $0.id == streamingAssistantMessageID })?.content
                ?? ""
            let content = ChatImageAttachment.preserveImageSnippets(
                from: streamed,
                in: finalText,
            )
            finalizeStreamingAssistant(content)
            currentToolOutcomes = (result["tool_outcomes"] as? [[String: Any]] ?? [])
                .map(ToolOutcome.init(dictionary:))
            collectTurnApprovalIDs(from: currentToolOutcomes)
            return true
        case "interrupted":
            let content = resolvedAssistantContent(
                partial: payload["partial_content"] as? String,
                fallback: "[turn interrupted: \(payload["reason"] as? String ?? "cancelled")]",
            )
            finalizeStreamingAssistant(content)
            currentToolOutcomes = (payload["partial_outcomes"] as? [[String: Any]] ?? [])
                .map(ToolOutcome.init(dictionary:))
            collectTurnApprovalIDs(from: currentToolOutcomes)
            return true
        case "error":
            throw NSError(
                domain: "CapDepMac",
                code: 2,
                userInfo: [
                    NSLocalizedDescriptionKey: payload["message"] as? String ?? "Turn failed.",
                ],
            )
        default:
            return false
        }
    }

    private func pollTurnUntilFinished(turnID: String) async throws {
        var cursor = 0
        let deadline = Date().addingTimeInterval(300)
        while true {
            if Date() > deadline {
                throw NSError(
                    domain: "CapDepMac",
                    code: 3,
                    userInfo: [
                        NSLocalizedDescriptionKey: "Turn did not finish within 5 minutes.",
                    ],
                )
            }
            let observed = try await client.call(
                method: "session.turn.get",
                params: ["turn_id": turnID],
            ) as? [String: Any]
            let observedTurn = observed?["turn"] as? [String: Any] ?? [:]
            let status = observedTurn["status"] as? String ?? ""
            let events = try await client.call(
                method: "session.turn.events",
                params: ["turn_id": turnID, "cursor": cursor, "limit": 200],
            ) as? [String: Any]
            let batch = events?["events"] as? [[String: Any]] ?? []
            cursor = events?["next_cursor"] as? Int ?? cursor
            for event in batch {
                if let partial = observedTurn["partial_content"] as? String, !partial.isEmpty {
                    setStreamingAssistantContent(partial)
                }
                if try applyTurnEvent(event) {
                    return
                }
            }
            if status == "completed" {
                let result = observedTurn["result"] as? [String: Any] ?? [:]
                let content = result["content"] as? String
                    ?? chatMessages.last(where: { $0.id == streamingAssistantMessageID })?.content
                    ?? ""
                finalizeStreamingAssistant(content)
                currentToolOutcomes = (result["tool_outcomes"] as? [[String: Any]] ?? [])
                    .map(ToolOutcome.init(dictionary:))
                collectTurnApprovalIDs(from: currentToolOutcomes)
                return
            }
            if status == "interrupted" {
                let content = resolvedAssistantContent(
                    partial: observedTurn["partial_content"] as? String,
                    fallback: "[turn interrupted: \(observedTurn["cancel_reason"] as? String ?? "cancelled")]",
                )
                finalizeStreamingAssistant(content)
                currentToolOutcomes = (observedTurn["partial_outcomes"] as? [[String: Any]] ?? [])
                    .map(ToolOutcome.init(dictionary:))
                collectTurnApprovalIDs(from: currentToolOutcomes)
                return
            }
            if status == "error" {
                throw NSError(
                    domain: "CapDepMac",
                    code: 2,
                    userInfo: [
                        NSLocalizedDescriptionKey: observedTurn["error"] as? String
                            ?? "Turn failed.",
                    ],
                )
            }
            if !turnStatusLine.hasPrefix("Waiting for daemon") {
                turnStatusLine = "Waiting for daemon turn result…"
            }
            try await Task.sleep(for: .milliseconds(80))
        }
    }

    private func describeTurnEvent(_ event: [String: Any]) -> String {
        let type = event["type"] as? String ?? ""
        let payload = event["payload"] as? [String: Any] ?? [:]
        switch type {
        case "model_selected":
            let role = payload["role"] as? String ?? ""
            let reason = payload["reason"] as? String ?? ""
            return "Selected \(modelRoleTitle(role)) model\(reason.isEmpty ? "" : " (\(reason))")…"
        case "llm_request_sent":
            let tools = payload["n_tools"] as? Int ?? 0
            if !currentResolvedModelRole.isEmpty {
                return "Asking \(modelRoleTitle(currentResolvedModelRole)) model (\(tools) tools available)…"
            }
            return "Asking \(selectedChatModelMode.title) model (\(tools) tools available)…"
        case "llm_token":
            return "Writing response…"
        case "llm_response_received":
            let toolCalls = payload["n_tool_calls"] as? Int ?? 0
            return "Model responded (\(toolCalls) tool call(s))…"
        case "tool_dispatched":
            return "Calling \(payload["tool_name"] as? String ?? "tool")…"
        case "tool_returned":
            let outcome = payload["outcome"] as? [String: Any] ?? [:]
            let toolName = outcome["tool_name"] as? String ?? payload["tool"] as? String ?? "tool"
            let decision = outcome["decision"] as? String ?? "done"
            if decision == "require_approval", let approvalID = outcome["approval_id"] {
                return "\(toolName) needs approval #\(approvalID)"
            }
            return "\(toolName) returned (\(decision))"
        case "image_attachment":
            return "Rendering image attachment…"
        case "completed":
            return "Turn completed."
        case "interrupted":
            return "Turn interrupted."
        case "error":
            return payload["message"] as? String ?? "Turn error."
        default:
            return type.isEmpty ? "Working…" : type
        }
    }

    private func modelRoleTitle(_ role: String) -> String {
        switch role {
        case "planner.fast":
            return "Fast"
        case "planner.tools":
            return "Tools"
        case "planner.quality":
            return "Quality"
        default:
            return role.isEmpty ? "Auto" : role
        }
    }

    func refreshFrontmostContext() async {
        do {
            let result = try await client.call(method: "macos.frontmost_context") as? [String: Any]
            let chips = result?["chips"] as? [[String: Any]] ?? []
            let mapped = chips.map { raw in
                ContextChip(
                    title: raw["title"] as? String ?? "",
                    detail: raw["detail"] as? String ?? "",
                    kind: raw["kind"] as? String ?? "",
                    isSensitive: raw["is_sensitive"] as? Bool ?? false,
                    isUntrusted: raw["is_untrusted"] as? Bool ?? false,
                )
            }
            if !mapped.isEmpty {
                contextChips = mapped
            }
        } catch {
            // Frontmost-app context depends on macOS Automation permission. Keep
            // the app usable if that permission has not been granted yet.
        }
    }

    func removeContextChip(_ chip: ContextChip) {
        contextChips.removeAll { $0.id == chip.id }
    }

    func addRelationshipMember(groupID: String, principalID: String) async {
        do {
            _ = try await client.call(
                method: "relationship_group.add_member",
                params: ["group_id": groupID, "principal_id": principalID],
            )
            await refresh()
        } catch {
            lastError = error.localizedDescription
        }
    }

    func removeRelationshipMember(groupID: String, principalID: String) async {
        do {
            _ = try await client.call(
                method: "relationship_group.remove_member",
                params: ["group_id": groupID, "principal_id": principalID],
            )
            await refresh()
        } catch {
            lastError = error.localizedDescription
        }
    }

    func promoteRelationshipMember(groupID: String, principalID: String, tier: String) async {
        do {
            _ = try await client.call(
                method: "relationship_group.promote",
                params: ["group_id": groupID, "principal_id": principalID, "tier": tier],
            )
            await refresh()
        } catch {
            lastError = error.localizedDescription
        }
    }

    func refreshRelationshipAudit(principalID: String) async -> [String: Any]? {
        do {
            return try await client.call(
                method: "relationship_group.aggregate_audit",
                params: ["principal_id": principalID],
            ) as? [String: Any]
        } catch {
            lastError = error.localizedDescription
            return nil
        }
    }

    func refreshApprovalDetail(_ approval: Approval) async {
        do {
            let result = try await client.call(
                method: "approval.detail",
                params: ["id": approval.id],
            ) as? [String: Any]
            approvalDetails[approval.id] = ApprovalDetail(dictionary: result ?? [:])
        } catch {
            lastError = error.localizedDescription
        }
    }

    func refreshSecurityContext(sessionID: String) async {
        guard !sessionID.isEmpty else {
            return
        }
        do {
            let result = try await client.call(
                method: "session.security_context",
                params: ["session_id": sessionID],
            ) as? [String: Any]
            sessionSecurityContexts[sessionID] = SessionSecurityContext(dictionary: result ?? [:])
        } catch {
            lastError = error.localizedDescription
        }
    }

    func showApproval(_ approval: Approval) async -> Approval? {
        do {
            let result = try await client.call(
                method: "approval.show",
                params: ["id": approval.id],
            ) as? [String: Any]
            return Approval(dictionary: result ?? [:])
        } catch {
            lastError = error.localizedDescription
            return nil
        }
    }

    func createApprovalPattern(action: String, targetPattern: String) async {
        do {
            _ = try await client.call(
                method: "approval_pattern.create",
                params: [
                    "action": action,
                    "target_pattern": targetPattern,
                    "created_by": "CapDepMac",
                    "ttl_hours": 720,
                ],
            )
            await refresh()
        } catch {
            lastError = error.localizedDescription
        }
    }

    func revokeApprovalPattern(_ pattern: ApprovalPatternViewData) async {
        do {
            _ = try await client.call(
                method: "approval_pattern.revoke",
                params: ["id": pattern.id],
            )
            await refresh()
        } catch {
            lastError = error.localizedDescription
        }
    }

    func importApprovalPatterns(path: String) async {
        do {
            _ = try await client.call(
                method: "approval_pattern.import",
                params: ["path": path],
            )
            await refresh()
        } catch {
            lastError = error.localizedDescription
        }
    }

    func googleOAuthStatus(for serviceID: String) -> GoogleOAuthStatus {
        googleOAuthStatuses[serviceID] ?? GoogleOAuthStatus(
            dictionary: ["service_id": serviceID, "display_name": serviceID],
        )
    }

    func presentGoogleOAuthWizard(serviceID: String? = nil) {
        googleOAuthWizardServiceID = serviceID ?? preferredGoogleOAuthServiceID()
        isGoogleOAuthWizardPresented = true
    }

    func dismissGoogleOAuthWizard() {
        isGoogleOAuthWizardPresented = false
        googleOAuthWizardServiceID = nil
    }

    func preferredGoogleOAuthServiceID() -> String {
        for connector in connectorStatuses where connector.id.hasPrefix("google-") {
            if connector.status != "connected" {
                return connector.id
            }
        }
        return connectorStatuses.first(where: { $0.id.hasPrefix("google-") })?.id ?? "google-gmail"
    }

    func restartDaemon() async {
        guard !isRecoveringDaemon else {
            return
        }
        isRecoveringDaemon = true
        setDaemonConnection(.reconnecting)
        defer {
            isRecoveringDaemon = false
        }
        do {
            try await daemonSupervisor.restart(client: client)
            _ = await verifyDaemonHandshake()
            await refresh()
        } catch {
            setDaemonConnection(.disconnected, detail: error.localizedDescription)
            lastError = error.localizedDescription
        }
    }

    func configureGoogleOAuth(
        serviceID: String,
        clientID: String,
        clientSecret: String,
    ) async {
        guard !isConfiguringGoogleOAuth else {
            return
        }
        isConfiguringGoogleOAuth = true
        defer {
            isConfiguringGoogleOAuth = false
        }
        do {
            let result = try await client.call(
                method: "setup.google.configure_oauth",
                params: [
                    "service_id": serviceID,
                    "client_id": clientID,
                    "client_secret": clientSecret,
                ],
            ) as? [String: Any]
            let status = GoogleOAuthStatus(dictionary: result ?? [:])
            googleOAuthStatuses[status.serviceID] = status
            if status.serviceID == "google-gmail" {
                gmailOAuthStatus = status
            }
            await refresh()
        } catch {
            lastError = error.localizedDescription
        }
    }

    func configureGmailOAuth(clientID: String, clientSecret: String) async {
        await configureGoogleOAuth(
            serviceID: "google-gmail",
            clientID: clientID,
            clientSecret: clientSecret,
        )
    }

    func updateSettings(_ settings: DaemonSettings) async {
        do {
            let result = try await client.call(
                method: "settings.update",
                params: ["settings": settings.rpcDictionary],
            ) as? [String: Any]
            daemonSettings = DaemonSettings(
                dictionary: result?["settings"] as? [String: Any] ?? [:],
            )
            await refresh()
        } catch {
            lastError = error.localizedDescription
        }
    }

    func selectImageProfile(_ profileID: String) async {
        do {
            let result = try await client.call(
                method: "image.profile.set",
                params: ["profile": profileID],
            ) as? [String: Any]
            imageReadiness = ImageReadiness(
                dictionary: result?["readiness"] as? [String: Any] ?? [:],
            )
            var updated = daemonSettings
            updated.imageProfile = result?["selected"] as? String ?? profileID
            daemonSettings = updated
            await refresh()
        } catch {
            lastError = error.localizedDescription
        }
    }

    func validateConfiguration() async {
        do {
            let result = try await client.call(method: "config.validate") as? [String: Any]
            configValidation = ConfigValidation(dictionary: result ?? [:])
        } catch {
            lastError = error.localizedDescription
        }
    }

    func refreshLogLocations() async {
        do {
            let result = try await client.call(method: "config.log_locations") as? [String: Any]
            logLocations = (
                (result?["logs"] as? [[String: Any]] ?? [])
                    + (result?["directories"] as? [[String: Any]] ?? [])
            )
            .map(LogLocation.init(dictionary:))
        } catch {
            lastError = error.localizedDescription
        }
    }

    func authorizeGoogleOAuth(serviceID: String) async {
        guard !isConfiguringGoogleOAuth else {
            return
        }
        isConfiguringGoogleOAuth = true
        defer {
            isConfiguringGoogleOAuth = false
        }
        do {
            let result = try await client.call(
                method: "setup.google.oauth_login",
                params: [
                    "service_id": serviceID,
                    "open_browser": true,
                    "timeout_seconds": 180,
                ],
            ) as? [String: Any]
            let status = GoogleOAuthStatus(dictionary: result ?? [:])
            googleOAuthStatuses[status.serviceID] = status
            if status.serviceID == "google-gmail" {
                gmailOAuthStatus = status
            }
            await refresh()
        } catch {
            lastError = error.localizedDescription
        }
    }

    func authorizeGmailOAuth() async {
        await authorizeGoogleOAuth(serviceID: "google-gmail")
    }

    func revokeGoogleOAuth(serviceID: String) async {
        guard !isConfiguringGoogleOAuth else {
            return
        }
        isConfiguringGoogleOAuth = true
        defer {
            isConfiguringGoogleOAuth = false
        }
        do {
            let result = try await client.call(
                method: "setup.google.oauth_revoke",
                params: ["service_id": serviceID],
            ) as? [String: Any]
            let status = GoogleOAuthStatus(dictionary: result ?? [:])
            googleOAuthStatuses[status.serviceID] = status
            if status.serviceID == "google-gmail" {
                gmailOAuthStatus = status
            }
            await refresh()
        } catch {
            lastError = error.localizedDescription
        }
    }

    func runSetupAction(_ action: SetupAction) async {
        if let serviceID = Self.serviceIDFromSetupAction(action.id),
           action.kind == "daemon_form"
               || action.kind == "daemon_browser_oauth"
               || action.id.contains("configure_oauth")
               || action.id.contains("oauth_login")
        {
            presentGoogleOAuthWizard(serviceID: serviceID)
            return
        }

        do {
            let result = try await client.call(
                method: "setup.run_action",
                params: ["action_id": action.id],
            ) as? [String: Any]
            if let method = result?["method"] as? String {
                if method == "setup.google.configure_oauth"
                    || method == "setup.google_gmail.configure_oauth"
                {
                    let serviceID = (result?["params"] as? [String: Any])?["service_id"] as? String
                        ?? Self.serviceIDFromSetupAction(action.id)
                    presentGoogleOAuthWizard(serviceID: serviceID)
                } else if method == "config.validate" {
                    await validateConfiguration()
                } else if method == "config.log_locations" {
                    await refreshLogLocations()
                } else if method == "setup.google_gmail.oauth_login" {
                    presentGoogleOAuthWizard(serviceID: "google-gmail")
                } else if method == "setup.google.oauth_login",
                          let serviceID = (result?["params"] as? [String: Any])?["service_id"]
                            as? String
                {
                    presentGoogleOAuthWizard(serviceID: serviceID)
                }
            } else if let url = result?["url"] as? String, let target = URL(string: url) {
                NSWorkspace.shared.open(target)
            } else if let section = result?["section"] as? String {
                if section == "trust" {
                    selectedSection = .trust
                } else if section == "setup" {
                    selectedSection = .setup
                }
            }
            await refresh()
        } catch {
            lastError = error.localizedDescription
        }
    }

    func setAutomationPaused(_ paused: Bool) async {
        do {
            let result = try await client.call(
                method: "runtime.automation_pause",
                params: ["paused": paused],
            ) as? [String: Any]
            runtimeControls = RuntimeControlState(
                dictionary: result?["runtime"] as? [String: Any] ?? [:],
            )
            await refresh()
        } catch {
            lastError = error.localizedDescription
        }
    }

    func requestScreenControl() async {
        do {
            let result = try await client.call(
                method: "runtime.screen_control.request",
                params: [
                    "session_id": currentSessionID ?? "",
                    "reason": "CapDepMac user requested generic screen control",
                ],
            ) as? [String: Any]
            runtimeControls = RuntimeControlState(
                dictionary: result?["runtime"] as? [String: Any] ?? [:],
            )
            daemonSettings = DaemonSettings(
                dictionary: result?["settings"] as? [String: Any] ?? daemonSettings.rpcDictionary,
            )
            await refresh()
        } catch {
            lastError = error.localizedDescription
        }
    }

    func upsertSourceBinding(
        name: String,
        scopePattern: String,
        category: String,
        tier: String,
    ) async {
        do {
            _ = try await client.call(
                method: "source_binding.upsert",
                params: [
                    "binding": [
                        "name": name,
                        "scope_pattern_canonical": scopePattern,
                        "category": category,
                        "default_tier": tier,
                        "write_discipline": "version-preserving",
                    ],
                ],
            )
            await refresh()
        } catch {
            lastError = error.localizedDescription
        }
    }

    func deleteSourceBinding(_ binding: SourceBindingViewData) async {
        do {
            _ = try await client.call(
                method: "source_binding.delete",
                params: ["name": binding.name],
            )
            await refresh()
        } catch {
            lastError = error.localizedDescription
        }
    }

    func deferApproval(_ approval: Approval) async {
        do {
            _ = try await client.call(method: "approval.defer", params: ["id": approval.id])
            await refresh()
        } catch {
            lastError = error.localizedDescription
        }
    }

    func approveGroup(_ approval: Approval) async {
        guard !approval.siblingGroupID.isEmpty else {
            return
        }
        let strongAuth = await strongAuthMarkerIfNeeded(for: approval)
        guard strongAuthSatisfied(for: approval, marker: strongAuth) else {
            return
        }
        do {
            _ = try await client.call(
                method: "approval.approve_group",
                params: [
                    "group_id": approval.siblingGroupID,
                    "decided_by": "CapDepMac",
                    "strong_auth": strongAuth,
                ],
            )
            approvalWindowID = nil
            await refresh()
        } catch {
            lastError = error.localizedDescription
        }
    }

    var pendingGrantRecovery: RecoveryStep? {
        currentToolOutcomes
            .first(where: { $0.decision.lowercased() == "deny" })?
            .grantRecoveryStep
    }

    var pendingDeniedGrantOutcome: ToolOutcome? {
        currentToolOutcomes.first { outcome in
            outcome.decision.lowercased() == "deny" && outcome.grantRecoveryStep != nil
        }
    }

    func dismissGrantPrompt() {
        grantPromptPresented = false
    }

    private func presentApprovalPromptIfNeeded(approvalID: Int) {
        focusedApprovalID = approvalID
        approvalWindowID = approvalID
        selectedSection = .approvals
        grantPromptPresented = false
    }

    private func presentPolicyPromptsAfterTurn() {
        if let firstApproval = turnPendingApprovalIDs.first {
            presentApprovalPromptIfNeeded(approvalID: firstApproval)
            return
        }
        grantPromptPresented = pendingGrantRecovery != nil
    }

    private func noteGrantRetryIfNeeded() {
        guard pendingGrantRecovery != nil else {
            pendingGrantRetryMessage = nil
            return
        }
        if let lastUser = chatMessages.last(where: { $0.role == .user })?.content,
           !lastUser.isEmpty {
            pendingGrantRetryMessage = lastUser
        }
    }

    func grantCapability(from step: RecoveryStep, sessionID: String) async {
        await grantCapability(from: step, sessionID: sessionID, retryMessage: nil)
    }

    func grantCapabilityAndRetry(from step: RecoveryStep, sessionID: String) async {
        await grantCapability(from: step, sessionID: sessionID, retryMessage: pendingGrantRetryMessage)
    }

    private func grantCapability(
        from step: RecoveryStep,
        sessionID: String,
        retryMessage: String?,
    ) async {
        guard let kind = step.grantKind, let pattern = step.guiGrantPattern() else {
            lastError = "This recovery step cannot be granted from the GUI."
            return
        }
        let allowsDestructive = step.args.contains("--destructive")
        let expiry: String
        if step.prefersSessionGrantFromGUI {
            expiry = "session"
        } else {
            expiry = step.isOneShot ? "one_shot" : "session"
        }
        let capability: [String: Any] = [
            "kind": kind,
            "pattern": pattern,
            "expiry": expiry,
            "origin": "user_approved",
            "audit_id": UUID().uuidString,
            "allows_destructive": allowsDestructive,
            "revoked_by": [] as [String],
        ]
        do {
            _ = try await client.call(
                method: allowsDestructive ? "operator.grant_capability" : "session.grant_capability",
                params: [
                    "session_id": sessionID,
                    "capability": capability,
                ],
            )
            lastError = nil
            currentToolOutcomes = []
            pendingGrantRetryMessage = nil
            grantPromptPresented = false
            if let retryMessage, !retryMessage.isEmpty {
                if chatMessages.last?.role == .assistant {
                    chatMessages.removeLast()
                }
                await runTurn(message: retryMessage, sessionID: sessionID, appendUserMessage: false)
            } else {
                await refresh()
            }
        } catch {
            lastError = error.localizedDescription
        }
    }

    func cancelCurrentTurn() async {
        if let currentTurnID {
            do {
                _ = try await client.call(
                    method: "session.turn.cancel",
                    params: ["turn_id": currentTurnID, "reason": "operator_stop"],
                )
            } catch {
                lastError = error.localizedDescription
            }
            return
        }
        guard let currentSessionID else {
            return
        }
        do {
            _ = try await client.call(
                method: "session.cancel",
                params: ["session_id": currentSessionID],
            )
        } catch {
            lastError = error.localizedDescription
        }
    }

    func refreshCurrentSessionChildren() async {
        guard let currentSessionID else {
            selectedSessionChildren = []
            return
        }
        do {
            let result = try await client.call(
                method: "session.children",
                params: ["session_id": currentSessionID],
            ) as? [String: Any]
            selectedSessionChildren = (result?["sessions"] as? [[String: Any]] ?? [])
                .map(CapDepSession.init(dictionary:))
        } catch {
            lastError = error.localizedDescription
        }
    }

    func addLabelsToCurrentSession(_ labels: [String]) async {
        guard let currentSessionID else {
            return
        }
        do {
            _ = try await client.call(
                method: "session.add_labels",
                params: ["session_id": currentSessionID, "labels": labels],
            )
            await refresh()
        } catch {
            lastError = error.localizedDescription
        }
    }

    func setCurrentSessionEnforcement(_ mode: String) async {
        guard let currentSessionID else {
            return
        }
        do {
            _ = try await client.call(
                method: "session.set_enforcement",
                params: ["session_id": currentSessionID, "mode": mode],
            )
            await refresh()
        } catch {
            lastError = error.localizedDescription
        }
    }

    func setCurrentSessionFirstUsePrompts(enabled: Bool) async {
        guard let currentSessionID else {
            return
        }
        do {
            _ = try await client.call(
                method: "session.set_first_use_prompts",
                params: ["session_id": currentSessionID, "enabled": enabled],
            )
            await refresh()
        } catch {
            lastError = error.localizedDescription
        }
    }

    private func decide(_ approval: Approval, approved: Bool) async {
        var strongAuth = ""
        if approved {
            strongAuth = await strongAuthMarkerIfNeeded(for: approval)
            guard strongAuthSatisfied(for: approval, marker: strongAuth) else {
                return
            }
        }
        do {
            var params: [String: Any] = [
                "id": approval.id,
                "decided_by": "CapDepMac",
            ]
            if approved {
                params["strong_auth"] = strongAuth
            } else {
                params["reason"] = "denied in CapDep macOS app"
            }
            _ = try await client.call(
                method: approved ? "approval.approve" : "approval.deny",
                params: params,
            )
            approvalWindowID = nil
            await refresh()
        } catch {
            lastError = error.localizedDescription
        }
    }

    private func strongAuthSatisfied(for approval: Approval, marker: String) -> Bool {
        guard approval.touchIDPolicyEnabled else {
            return true
        }
        guard approval.requiresStrongAuth else {
            return true
        }
        return marker == "touch_id"
    }

    private func strongAuthMarkerIfNeeded(for approval: Approval) async -> String {
        guard approval.touchIDPolicyEnabled else {
            return ""
        }
        guard approval.requiresStrongAuth else {
            return ""
        }
        let context = LAContext()
        var error: NSError?
        guard context.canEvaluatePolicy(.deviceOwnerAuthentication, error: &error) else {
            lastError = error?.localizedDescription ?? "Touch ID or device authentication is unavailable."
            return ""
        }
        do {
            let ok = try await context.evaluatePolicy(
                .deviceOwnerAuthentication,
                localizedReason: "Approve high-risk CapDep action #\(approval.id)",
            )
            return ok ? "touch_id" : ""
        } catch {
            lastError = error.localizedDescription
            return ""
        }
    }

    func explainPolicy(sessionID: String? = nil) async -> [String: Any]? {
        var params: [String: Any] = [:]
        if let sessionID {
            params["session_id"] = sessionID
        }
        do {
            return try await client.call(method: "policy.explain", params: params) as? [String: Any]
        } catch {
            lastError = error.localizedDescription
            return nil
        }
    }

    func validatePolicy() async -> [String: Any]? {
        do {
            return try await client.call(method: "policy.validate") as? [String: Any]
        } catch {
            lastError = error.localizedDescription
            return nil
        }
    }

    func daemonVersion() async -> String {
        do {
            let result = try await client.call(method: "version") as? [String: Any]
            return result?["version"] as? String ?? ""
        } catch {
            lastError = error.localizedDescription
            return ""
        }
    }

    func testTool(_ tool: DaemonToolViewData, sessionID: String, args: [String: Any]) async -> [String: Any]? {
        do {
            return try await client.call(
                method: "tool.test",
                params: ["tool": tool.name, "session_id": sessionID, "args": args],
            ) as? [String: Any]
        } catch {
            lastError = error.localizedDescription
            return nil
        }
    }

    func callTool(_ tool: DaemonToolViewData, sessionID: String, args: [String: Any]) async -> [String: Any]? {
        do {
            return try await client.call(
                method: "tool.call",
                params: ["tool": tool.name, "session_id": sessionID, "args": args],
            ) as? [String: Any]
        } catch {
            lastError = error.localizedDescription
            return nil
        }
    }

    func callDaemonRPC(method: String, paramsJSON: String) async -> String {
        let trimmedMethod = method.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmedMethod.isEmpty else {
            return "Method is required."
        }
        do {
            let params = try Self.parseJSONObject(paramsJSON)
            let result = try await client.call(method: trimmedMethod, params: params)
            return Self.prettyJSON(result)
        } catch {
            lastError = error.localizedDescription
            return "Error: \(error.localizedDescription)"
        }
    }

    nonisolated private static func parseJSONObject(_ text: String) throws -> [String: Any] {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty {
            return [:]
        }
        let data = Data(trimmed.utf8)
        let object = try JSONSerialization.jsonObject(with: data)
        guard let dictionary = object as? [String: Any] else {
            throw DaemonRPCWorkbenchError.paramsMustBeObject
        }
        return dictionary
    }

    nonisolated private static func prettyJSON(_ value: Any) -> String {
        guard JSONSerialization.isValidJSONObject(value),
              let data = try? JSONSerialization.data(
                withJSONObject: value,
                options: [.prettyPrinted, .sortedKeys],
              ),
              let rendered = String(data: data, encoding: .utf8)
        else {
            return "\(value)"
        }
        return rendered
    }

    func showOverride(_ grant: OverrideGrantViewData) async -> [String: Any]? {
        do {
            return try await client.call(
                method: "override.show",
                params: ["grant_id": grant.id],
            ) as? [String: Any]
        } catch {
            lastError = error.localizedDescription
            return nil
        }
    }

    func refuseOverride(_ grant: OverrideGrantViewData) async {
        do {
            _ = try await client.call(
                method: "override.refuse",
                params: ["grant_id": grant.id],
            )
            await refresh()
        } catch {
            lastError = error.localizedDescription
        }
    }

    func pauseCurrentSession() async {
        guard let currentSessionID else {
            return
        }
        await updateCurrentSession(method: "session.pause", sessionID: currentSessionID)
    }

    func resumeCurrentSession() async {
        guard let currentSessionID else {
            return
        }
        await updateCurrentSession(method: "session.resume", sessionID: currentSessionID)
    }

    func abortCurrentSession() async {
        guard let currentSessionID else {
            return
        }
        await updateCurrentSession(method: "session.abort", sessionID: currentSessionID)
    }

    private func updateCurrentSession(method: String, sessionID: String) async {
        do {
            _ = try await client.call(method: method, params: ["session_id": sessionID])
            await refresh()
        } catch {
            lastError = error.localizedDescription
        }
    }

    private static func serviceIDFromSetupAction(_ actionID: String) -> String? {
        let prefix = "setup.google."
        guard actionID.hasPrefix(prefix) else {
            return nil
        }
        let remainder = actionID.dropFirst(prefix.count)
        guard let dot = remainder.firstIndex(of: ".") else {
            return nil
        }
        let serviceID = String(remainder[..<dot])
        return serviceID.isEmpty ? nil : serviceID
    }

    private func shouldRecoverDaemon(after error: Error) -> Bool {
        if isRecoveringDaemon {
            return false
        }
        guard let clientError = error as? DaemonClientError else {
            return false
        }
        switch clientError {
        case .connectFailed, .responseClosed:
            return true
        default:
            return false
        }
    }
}

enum DaemonRPCWorkbenchError: LocalizedError {
    case paramsMustBeObject

    var errorDescription: String? {
        switch self {
        case .paramsMustBeObject:
            return "params JSON must be an object"
        }
    }
}
