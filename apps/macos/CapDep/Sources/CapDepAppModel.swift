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
    @Published private(set) var provenanceNodes: [ProvenanceNode] = []
    @Published private(set) var provenanceEdges: [ProvenanceEdge] = []
    @Published private(set) var relationshipGroups: [RelationshipGroupViewData] = []
    @Published private(set) var approvalPatterns: [ApprovalPatternViewData] = []
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
    private var didStart = false
    private var lastPendingApprovalCount = 0

    func start() async {
        guard !didStart else {
            return
        }
        didStart = true
        await notifications.requestAuthorizationIfNeeded()
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
            async let provenanceResult = client.call(method: "provenance.graph")
            async let relationshipResult = client.call(method: "relationship_group.list")
            async let patternsResult = client.call(method: "approval_pattern.list")

            let approvalsObject = try await approvalsResult as? [String: Any]
            let sessionsObject = try await sessionsResult as? [String: Any]
            let auditObject = try await auditResult as? [String: Any]
            let statusObject = try await statusResult as? [String: Any]
            let setupObject = try await setupResult as? [String: Any]
            let provenanceObject = try await provenanceResult as? [String: Any]
            let relationshipObject = try await relationshipResult as? [String: Any]
            let patternsObject = try await patternsResult as? [String: Any]

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
            provenanceNodes = (provenanceObject?["nodes"] as? [[String: Any]] ?? [])
                .map(ProvenanceNode.init(dictionary:))
            provenanceEdges = (provenanceObject?["edges"] as? [[String: Any]] ?? [])
                .map(ProvenanceEdge.init(dictionary:))
            relationshipGroups = (relationshipObject?["groups"] as? [[String: Any]] ?? [])
                .map(RelationshipGroupViewData.init(dictionary:))
            approvalPatterns = (patternsObject?["patterns"] as? [[String: Any]] ?? [])
                .map(ApprovalPatternViewData.init(dictionary:))
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
        }
    }

    func approve(_ approval: Approval) async {
        await decide(approval, approved: true)
    }

    func deny(_ approval: Approval) async {
        await decide(approval, approved: false)
    }

    func createSession(intent: String, purpose: Purpose? = nil) async {
        let chosenPurpose = purpose ?? selectedPurpose
        do {
            _ = try await client.call(
                method: "session.new",
                params: [
                    "intent": intent,
                    "owner": "CapDepMac",
                    "purpose_handle": chosenPurpose.rawValue,
                ],
            )
            selectedSection = .sessions
            await refresh()
        } catch {
            lastError = error.localizedDescription
        }
    }

    func launchWorkflow(_ workflow: WorkflowTemplate) async {
        selectedPurpose = workflow.purpose
        commandText = workflow.prompt
        await createSession(intent: workflow.prompt, purpose: workflow.purpose)
    }

    func submitCommand() async {
        let trimmed = commandText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            return
        }
        await createSession(intent: trimmed, purpose: selectedPurpose)
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
}
