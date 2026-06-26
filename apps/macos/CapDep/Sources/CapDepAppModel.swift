import AppKit
import Foundation
import LocalAuthentication

@MainActor
final class CapDepAppModel: ObservableObject {
    @Published private(set) var connected = false
    @Published private(set) var isRefreshing = false
    @Published private(set) var pendingApprovals: [Approval] = []
    @Published private(set) var sessions: [CapDepSession] = []
    @Published private(set) var events: [AuditEvent] = []
    @Published private(set) var appStatus = AppStatus.empty
    @Published private(set) var setupChecks: [SetupCheck] = []
    @Published private(set) var setupPlan = SetupPlan(dictionary: [:])
    @Published var approvalWindowID: Int?
    @Published private(set) var turnStatusLine = ""
    @Published private(set) var gmailOAuthStatus = GmailOAuthStatus.empty
    @Published private(set) var googleOAuthStatuses: [String: GoogleOAuthStatus] = [:]
    @Published private(set) var daemonSettings = DaemonSettings.empty
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
    @Published private(set) var currentSessionID: String?
    @Published private(set) var currentAssistantOutput = ""
    @Published private(set) var currentToolOutcomes: [ToolOutcome] = []
    @Published private(set) var isRunningTurn = false
    @Published private(set) var currentTurnID: String?
    @Published private(set) var turnPendingApprovalIDs: [Int] = []
    @Published private(set) var isRecoveringDaemon = false
    @Published private(set) var isConfiguringGoogleOAuth = false
    @Published var selectedSection: DashboardSection = .today
    @Published var selectedPurpose: Purpose = .general
    @Published var focusedApprovalID: Int?
    @Published var focusedSessionID: String?
    @Published var commandText = ""
    @Published var contextChips: [ContextChip] = [
        ContextChip(
            title: "Frontmost app",
            detail: "Not connected yet",
            kind: "macOS",
            isSensitive: false,
            isUntrusted: false,
        ),
    ]
    @Published var taskPanelPinned = false
    @Published var lastError: String?
    @Published var googleOAuthWizardServiceID: String?
    @Published var isGoogleOAuthWizardPresented = false

    let client = DaemonClient(socketPath: DaemonClient.defaultSocketPath())
    @Published private(set) var workflows: [WorkflowTemplate] = []
    private let notifications = NotificationCenterBridge()
    private let daemonSupervisor = DaemonSupervisor()
    private var didStart = false
    private var lastPendingApprovalCount = 0
    private var lastNotifiedApprovalID: Int?

    init() {
        Task {
            await start()
        }
    }

    func start() async {
        guard !didStart else {
            return
        }
        didStart = true
        await notifications.requestAuthorizationIfNeeded()
        await ensureDaemonRunning()
        await refresh()
        Task {
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(5))
                await refresh()
            }
        }
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

            let approvalsObject = try await approvalsResult as? [String: Any]
            let sessionsObject = try await sessionsResult as? [String: Any]
            let auditObject = try await auditResult as? [String: Any]
            let statusObject = try await statusResult as? [String: Any]
            let setupObject = try await setupResult as? [String: Any]
            let workflowsObject = try await workflowsResult as? [String: Any]
            let googleOAuthObject = try await googleOAuthResult as? [String: Any]
            let settingsObject = try await settingsResult as? [String: Any]
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
            connected = true
            lastError = nil
            await refreshFrontmostContext()
            if approvals.count > lastPendingApprovalCount, let first = approvals.first {
                await notifications.notifyPendingApproval(
                    count: approvals.count,
                    approvalID: first.id,
                )
                lastNotifiedApprovalID = first.id
            }
            lastPendingApprovalCount = approvals.count
        } catch {
            connected = false
            lastError = error.localizedDescription
            if shouldRecoverDaemon(after: error) {
                Task {
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
        defer {
            isRecoveringDaemon = false
        }
        do {
            try await daemonSupervisor.ensureRunning(client: client)
            connected = true
            lastError = nil
        } catch {
            connected = false
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
        focusedSessionID = session.id
        selectedSection = .sessions
        Task {
            await refreshSecurityContext(sessionID: session.id)
        }
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
            await refresh()
            return session
        } catch {
            lastError = error.localizedDescription
            return nil
        }
    }

    func launchWorkflow(_ workflow: WorkflowTemplate) async {
        selectedPurpose = workflow.purpose
        commandText = workflow.turnMessage
        await submitCommand(purpose: workflow.purpose, forceNewSession: false)
    }

    func submitCommand(purpose: Purpose? = nil, forceNewSession: Bool = false) async {
        let trimmed = commandText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            return
        }
        let chosenPurpose = purpose ?? selectedPurpose
        guard let session = await ensureSession(
            intent: trimmed,
            purpose: chosenPurpose,
            forceNew: forceNewSession,
        ) else {
            return
        }
        await send(message: trimmed, sessionID: session.id)
        commandText = ""
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
            await refresh()
            return session
        } catch {
            lastError = error.localizedDescription
            return nil
        }
    }

    func send(message: String, sessionID: String) async {
        isRunningTurn = true
        currentAssistantOutput = ""
        currentToolOutcomes = []
        turnPendingApprovalIDs = []
        turnStatusLine = "Starting turn…"
        currentTurnID = nil
        defer {
            isRunningTurn = false
            turnStatusLine = ""
            currentTurnID = nil
        }
        do {
            let start = try await client.call(
                method: "session.turn.start",
                params: [
                    "session_id": sessionID,
                    "message": message,
                    "client_id": "CapDepMac",
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
            currentTurnID = turnID
            currentSessionID = sessionID

            var cursor = 0
            while true {
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
                    turnStatusLine = describeTurnEvent(event)
                    if let partial = observedTurn["partial_content"] as? String, !partial.isEmpty {
                        currentAssistantOutput = partial
                    }
                    noteTurnApprovalRequest(from: event)
                }
                if status == "completed" {
                    let result = observedTurn["result"] as? [String: Any] ?? [:]
                    currentAssistantOutput = result["content"] as? String ?? currentAssistantOutput
                    currentToolOutcomes = (result["tool_outcomes"] as? [[String: Any]] ?? [])
                        .map(ToolOutcome.init(dictionary:))
                    collectTurnApprovalIDs(from: currentToolOutcomes)
                    break
                }
                if status == "interrupted" {
                    currentAssistantOutput = observedTurn["partial_content"] as? String
                        ?? "[turn interrupted: \(observedTurn["cancel_reason"] as? String ?? "cancelled")]"
                    currentToolOutcomes = (observedTurn["partial_outcomes"] as? [[String: Any]] ?? [])
                        .map(ToolOutcome.init(dictionary:))
                    collectTurnApprovalIDs(from: currentToolOutcomes)
                    break
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
                try await Task.sleep(for: .milliseconds(250))
            }
            await refresh()
            if let firstApproval = turnPendingApprovalIDs.first {
                focusedApprovalID = firstApproval
                approvalWindowID = firstApproval
                selectedSection = .approvals
            }
        } catch {
            lastError = error.localizedDescription
        }
    }

    private func collectTurnApprovalIDs(from outcomes: [ToolOutcome]) {
        for outcome in outcomes where outcome.decision == "require_approval" {
            if let approvalID = outcome.approvalID, !turnPendingApprovalIDs.contains(approvalID) {
                turnPendingApprovalIDs.append(approvalID)
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
            }
        } else if let rawID = outcome["approval_id"] as? NSNumber {
            let approvalID = rawID.intValue
            if !turnPendingApprovalIDs.contains(approvalID) {
                turnPendingApprovalIDs.append(approvalID)
            }
        }
    }

    private func describeTurnEvent(_ event: [String: Any]) -> String {
        let type = event["type"] as? String ?? ""
        let payload = event["payload"] as? [String: Any] ?? [:]
        switch type {
        case "llm_request_sent":
            let tools = payload["n_tools"] as? Int ?? 0
            return "Asking model (\(tools) tools available)…"
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
        defer {
            isRecoveringDaemon = false
        }
        do {
            try await daemonSupervisor.restart(client: client)
            connected = true
            lastError = nil
            await refresh()
        } catch {
            connected = false
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
