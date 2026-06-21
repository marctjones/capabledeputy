import Foundation

@MainActor
final class CapDepAppModel: ObservableObject {
    @Published private(set) var connected = false
    @Published private(set) var isRefreshing = false
    @Published private(set) var pendingApprovals: [Approval] = []
    @Published private(set) var sessions: [CapDepSession] = []
    @Published private(set) var events: [AuditEvent] = []
    @Published private(set) var appStatus = AppStatus.empty
    @Published private(set) var setupChecks: [SetupCheck] = []
    @Published private(set) var gmailOAuthStatus = GmailOAuthStatus.empty
    @Published private(set) var daemonSettings = DaemonSettings.empty
    @Published private(set) var configValidation = ConfigValidation.empty
    @Published private(set) var logLocations: [LogLocation] = []
    @Published private(set) var provenanceNodes: [ProvenanceNode] = []
    @Published private(set) var provenanceEdges: [ProvenanceEdge] = []
    @Published private(set) var relationshipGroups: [RelationshipGroupViewData] = []
    @Published private(set) var approvalPatterns: [ApprovalPatternViewData] = []
    @Published private(set) var memoryEntries: [MemoryEntryViewData] = []
    @Published private(set) var daemonTools: [DaemonToolViewData] = []
    @Published private(set) var overrideGrants: [OverrideGrantViewData] = []
    @Published private(set) var selectedSessionChildren: [CapDepSession] = []
    @Published private(set) var approvalDetails: [Int: ApprovalDetail] = [:]
    @Published private(set) var currentSessionID: String?
    @Published private(set) var currentAssistantOutput = ""
    @Published private(set) var currentToolOutcomes: [ToolOutcome] = []
    @Published private(set) var isRunningTurn = false
    @Published private(set) var isRecoveringDaemon = false
    @Published private(set) var isConfiguringGmailOAuth = false
    @Published var selectedSection: DashboardSection = .today
    @Published var selectedPurpose: Purpose = .general
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

    let client = DaemonClient(socketPath: DaemonClient.defaultSocketPath())
    let workflows = defaultWorkflowTemplates
    private let notifications = NotificationCenterBridge()
    private let daemonSupervisor = DaemonSupervisor()
    private var didStart = false
    private var lastPendingApprovalCount = 0

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
            async let setupResult = client.call(method: "setup.status")
            async let gmailOAuthResult = client.call(method: "setup.google_gmail.oauth_status")
            async let settingsResult = client.call(method: "settings.get")
            async let validationResult = client.call(method: "config.validate")
            async let logLocationsResult = client.call(method: "config.log_locations")
            async let provenanceResult = client.call(method: "provenance.graph")
            async let relationshipResult = client.call(method: "relationship_group.list")
            async let patternsResult = client.call(method: "approval_pattern.list")
            async let memoryResult = client.call(method: "memory.entries")
            async let toolsResult = client.call(method: "tool.list")
            async let overridesResult = client.call(method: "override.list")

            let approvalsObject = try await approvalsResult as? [String: Any]
            let sessionsObject = try await sessionsResult as? [String: Any]
            let auditObject = try await auditResult as? [String: Any]
            let statusObject = try await statusResult as? [String: Any]
            let setupObject = try await setupResult as? [String: Any]
            let gmailOAuthObject = try await gmailOAuthResult as? [String: Any]
            let settingsObject = try await settingsResult as? [String: Any]
            let validationObject = try await validationResult as? [String: Any]
            let logLocationsObject = try await logLocationsResult as? [String: Any]
            let provenanceObject = try await provenanceResult as? [String: Any]
            let relationshipObject = try await relationshipResult as? [String: Any]
            let patternsObject = try await patternsResult as? [String: Any]
            let memoryObject = try await memoryResult as? [String: Any]
            let toolsObject = try await toolsResult as? [String: Any]
            let overridesObject = try await overridesResult as? [String: Any]

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
            setupChecks = (setupObject?["checks"] as? [[String: Any]] ?? [])
                .map(SetupCheck.init(dictionary:))
            gmailOAuthStatus = GmailOAuthStatus(dictionary: gmailOAuthObject ?? [:])
            daemonSettings = DaemonSettings(
                dictionary: settingsObject?["settings"] as? [String: Any] ?? [:],
            )
            configValidation = ConfigValidation(dictionary: validationObject ?? [:])
            logLocations = (
                (logLocationsObject?["logs"] as? [[String: Any]] ?? [])
                    + (logLocationsObject?["directories"] as? [[String: Any]] ?? [])
            )
            .map(LogLocation.init(dictionary:))
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
            connected = true
            lastError = nil
            await refreshFrontmostContext()
            if approvals.count > lastPendingApprovalCount {
                await notifications.notifyPendingApprovals(count: approvals.count)
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
        commandText = workflow.prompt
        await submitCommand(purpose: workflow.purpose)
    }

    func submitCommand(purpose: Purpose? = nil) async {
        let trimmed = commandText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            return
        }
        guard let session = await createSession(intent: trimmed, purpose: purpose ?? selectedPurpose) else {
            return
        }
        await send(message: trimmed, sessionID: session.id)
    }

    func send(message: String, sessionID: String) async {
        isRunningTurn = true
        currentAssistantOutput = ""
        currentToolOutcomes = []
        defer {
            isRunningTurn = false
        }
        do {
            let result = try await client.call(
                method: "session.send",
                params: [
                    "session_id": sessionID,
                    "message": message,
                ],
            ) as? [String: Any]
            currentAssistantOutput = result?["content"] as? String ?? ""
            currentToolOutcomes = (result?["tool_outcomes"] as? [[String: Any]] ?? [])
                .map(ToolOutcome.init(dictionary:))
            await refresh()
        } catch {
            lastError = error.localizedDescription
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

    func configureGmailOAuth(clientID: String, clientSecret: String) async {
        guard !isConfiguringGmailOAuth else {
            return
        }
        isConfiguringGmailOAuth = true
        defer {
            isConfiguringGmailOAuth = false
        }
        do {
            let result = try await client.call(
                method: "setup.google_gmail.configure_oauth",
                params: [
                    "client_id": clientID,
                    "client_secret": clientSecret,
                ],
            ) as? [String: Any]
            gmailOAuthStatus = GmailOAuthStatus(dictionary: result ?? [:])
            await refresh()
        } catch {
            lastError = error.localizedDescription
        }
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

    func authorizeGmailOAuth() async {
        guard !isConfiguringGmailOAuth else {
            return
        }
        isConfiguringGmailOAuth = true
        defer {
            isConfiguringGmailOAuth = false
        }
        do {
            let result = try await client.call(
                method: "setup.google_gmail.oauth_login",
                params: [
                    "open_browser": true,
                    "timeout_seconds": 180,
                ],
            ) as? [String: Any]
            gmailOAuthStatus = GmailOAuthStatus(dictionary: result ?? [:])
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
        do {
            _ = try await client.call(
                method: "approval.approve_group",
                params: [
                    "group_id": approval.siblingGroupID,
                    "decided_by": "CapDepMac",
                ],
            )
            await refresh()
        } catch {
            lastError = error.localizedDescription
        }
    }

    func cancelCurrentTurn() async {
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

    private func decide(_ approval: Approval, approved: Bool) async {
        do {
            if approved {
                _ = try await client.call(
                    method: "approval.approve",
                    params: ["id": approval.id, "decided_by": "CapDepMac"],
                )
            } else {
                _ = try await client.call(
                    method: "approval.deny",
                    params: [
                        "id": approval.id,
                        "decided_by": "CapDepMac",
                        "reason": "denied in CapDep macOS app",
                    ],
                )
            }
            await refresh()
        } catch {
            lastError = error.localizedDescription
        }
    }

    private func updateCurrentSession(method: String, sessionID: String) async {
        do {
            _ = try await client.call(method: method, params: ["session_id": sessionID])
            await refresh()
        } catch {
            lastError = error.localizedDescription
        }
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
