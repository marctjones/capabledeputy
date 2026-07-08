import Foundation

struct Approval: Identifiable, Hashable {
    let id: Int
    let auditID: String
    let action: String
    let status: String
    let target: String
    let fromSession: String
    let toSession: String
    let justification: String
    let payload: String
    let labelsIn: [String]
    let labelsOut: [String]
    let siblingGroupID: String
    let rule: String
    let requiresStrongAuth: Bool
    let touchIDPolicyEnabled: Bool

    init(dictionary: [String: Any]) {
        self.id = dictionary["id"] as? Int ?? 0
        self.auditID = dictionary["audit_id"] as? String ?? ""
        self.action = dictionary["action"] as? String ?? ""
        self.status = dictionary["status"] as? String ?? ""
        self.target = dictionary["target"] as? String ?? ""
        self.fromSession = dictionary["from_session"] as? String ?? ""
        self.toSession = dictionary["to_session"] as? String ?? ""
        self.justification = dictionary["justification"] as? String ?? ""
        self.payload = dictionary["payload"] as? String ?? ""
        self.labelsIn = Self.labels(from: dictionary["labels_in"])
        self.labelsOut = Self.labels(from: dictionary["labels_out"])
        self.siblingGroupID = dictionary["sibling_group_id"] as? String ?? ""
        self.rule = dictionary["rule"] as? String ?? ""
        self.requiresStrongAuth = dictionary["requires_strong_auth"] as? Bool ?? false
        self.touchIDPolicyEnabled = dictionary["touch_id_policy_enabled"] as? Bool ?? false
    }

    private static func labels(from raw: Any?) -> [String] {
        if let values = raw as? [String] {
            return values.sorted()
        }
        if let labelState = raw as? [String: Any] {
            return CapDepSession.flattenLabels(labelState)
        }
        return []
    }

    var requiresHighRiskAuthentication: Bool {
        requiresStrongAuth
    }
}

struct ChatMessage: Identifiable, Hashable {
    enum Role: Hashable {
        case user
        case assistant
    }

    let id: String
    let role: Role
    var content: String
    var isStreaming: Bool

    init(
        id: String = UUID().uuidString,
        role: Role,
        content: String,
        isStreaming: Bool = false,
    ) {
        self.id = id
        self.role = role
        self.content = content
        self.isStreaming = isStreaming
    }

    static func fromHistory(_ history: [[String: Any]]) -> [ChatMessage] {
        history.enumerated().map { index, turn in
            let roleRaw = turn["role"] as? String ?? ""
            let role: Role = roleRaw == "user" ? .user : .assistant
            let turnID = turn["turn_id"]
            let id: String
            if let intID = turnID as? Int {
                id = "turn-\(intID)"
            } else if let numID = turnID as? NSNumber {
                id = "turn-\(numID.intValue)"
            } else {
                id = "turn-\(index)"
            }
            return ChatMessage(
                id: id,
                role: role,
                content: turn["content"] as? String ?? "",
            )
        }
    }
}

enum DaemonConnectionPhase: String, Hashable {
    case starting
    case connected
    case disconnected
    case reconnecting
    case unhealthy
    case incompatible

    var title: String {
        switch self {
        case .starting: "Starting"
        case .connected: "Connected"
        case .disconnected: "Disconnected"
        case .reconnecting: "Reconnecting"
        case .unhealthy: "Unhealthy"
        case .incompatible: "Contract mismatch"
        }
    }

    var systemImage: String {
        switch self {
        case .starting: "clock"
        case .connected: "checkmark.circle.fill"
        case .disconnected: "xmark.octagon.fill"
        case .reconnecting: "arrow.triangle.2.circlepath"
        case .unhealthy: "exclamationmark.triangle.fill"
        case .incompatible: "exclamationmark.shield.fill"
        }
    }

    var isUsable: Bool {
        self == .connected
    }
}

struct DaemonConnectionHealth: Hashable {
    let phase: DaemonConnectionPhase
    let version: String
    let socketPath: String
    let missingMethods: [String]
    let detail: String

    var isUsable: Bool {
        phase.isUsable && missingMethods.isEmpty
    }

    var statusTitle: String {
        if phase == .connected, !version.isEmpty {
            return "Connected \(version)"
        }
        return phase.title
    }

    static let empty = DaemonConnectionHealth(
        phase: .disconnected,
        version: "",
        socketPath: "",
        missingMethods: [],
        detail: "",
    )

    static func missingRequiredMethods(
        available: Set<String>,
        required: [String],
    ) -> [String] {
        required.filter { !available.contains($0) }.sorted()
    }
}

enum ChatPromptStatus: String, Hashable, Codable {
    case queued
    case running
    case completed
    case failed

    var title: String {
        switch self {
        case .queued: "Queued"
        case .running: "Running"
        case .completed: "Completed"
        case .failed: "Failed"
        }
    }
}

struct ChatPromptRun: Identifiable, Hashable, Codable {
    let id: String
    let displayMessage: String
    let daemonMessage: String
    let purpose: Purpose
    var sessionID: String?
    var turnID: String?
    var status: ChatPromptStatus
    var error: String?

    init(
        id: String = UUID().uuidString,
        displayMessage: String,
        daemonMessage: String,
        purpose: Purpose,
        sessionID: String? = nil,
        turnID: String? = nil,
        status: ChatPromptStatus = .queued,
        error: String? = nil,
    ) {
        self.id = id
        self.displayMessage = displayMessage
        self.daemonMessage = daemonMessage
        self.purpose = purpose
        self.sessionID = sessionID
        self.turnID = turnID
        self.status = status
        self.error = error
    }

    var isTerminal: Bool {
        status == .completed || status == .failed
    }
}

enum ChatModelMode: String, CaseIterable, Identifiable {
    case automatic
    case fast
    case tools
    case quality

    var id: String { rawValue }

    var title: String {
        switch self {
        case .automatic: "Auto"
        case .fast: "Fast"
        case .tools: "Tools"
        case .quality: "Quality"
        }
    }

    var systemImage: String {
        switch self {
        case .automatic: "wand.and.stars"
        case .fast: "bolt.fill"
        case .tools: "wrench.and.screwdriver"
        case .quality: "sparkles"
        }
    }

    var helpText: String {
        switch self {
        case .automatic: "Let CapDep choose the model for this turn"
        case .fast: "Prefer the snappy local chat model"
        case .tools: "Prefer the stronger tool-use model"
        case .quality: "Prefer the highest-quality local model"
        }
    }

    func daemonMessage(for text: String) -> String {
        switch self {
        case .automatic:
            return text
        case .fast:
            return "/fast \(text)"
        case .tools:
            return "/model tools \(text)"
        case .quality:
            return "/quality \(text)"
        }
    }
}

struct CapDepSession: Identifiable, Hashable {
    let id: String
    let status: String
    let intent: String
    let purpose: String
    let owner: String
    let labels: [String]

    init(dictionary: [String: Any]) {
        self.id = dictionary["id"] as? String ?? ""
        self.status = dictionary["status"] as? String ?? ""
        self.intent = dictionary["intent"] as? String ?? ""
        self.purpose = dictionary["purpose_handle"] as? String ?? ""
        self.owner = dictionary["owner"] as? String ?? ""
        if let labelState = dictionary["label_state"] as? [String: Any] {
            self.labels = Self.flattenLabels(labelState)
        } else {
            self.labels = []
        }
    }

    static func flattenLabels(_ labelState: [String: Any]) -> [String] {
        var labels: [String] = []
        for key in ["a", "b", "c", "d"] {
            if let values = labelState[key] as? [[String: Any]] {
                for value in values {
                    labels.append(value.compactMap { _, raw in "\(raw)" }.joined(separator: ":"))
                }
            } else if let values = labelState[key] as? [String] {
                labels.append(contentsOf: values)
            }
        }
        return labels.sorted()
    }
}

struct AuditEvent: Identifiable, Hashable {
    let id = UUID()
    let eventType: String
    let sessionId: String
    let payloadSummary: String

    init(dictionary: [String: Any]) {
        self.eventType = dictionary["event_type"] as? String ?? ""
        self.sessionId = dictionary["session_id"] as? String ?? ""
        if let payload = dictionary["payload"] as? [String: Any] {
            self.payloadSummary = payload
                .sorted { $0.key < $1.key }
                .prefix(4)
                .map { "\($0.key)=\($0.value)" }
                .joined(separator: "  ")
        } else {
            self.payloadSummary = ""
        }
    }
}

struct RecoveryStep: Identifiable, Hashable {
    let id = UUID()
    let command: String
    let args: [String]
    let rationale: String

    init(dictionary: [String: Any]) {
        self.command = dictionary["command"] as? String ?? ""
        self.args = dictionary["args"] as? [String] ?? []
        self.rationale = dictionary["rationale"] as? String ?? ""
    }

    var grantKind: String? {
        guard command == "/grant", let kind = args.first else {
            return nil
        }
        return kind
    }

    var grantPattern: String? {
        guard command == "/grant", args.count >= 2 else {
            return nil
        }
        return args[1]
    }

    var isOneShot: Bool {
        args.contains("--one-shot")
    }

    /// Widen one-shot engine recovery patterns so a GUI grant covers the
    /// directory subtree, not only a single file path.
    func guiGrantPattern() -> String? {
        guard let kind = grantKind, let pattern = grantPattern else {
            return nil
        }
        return RecoveryStep.widenedGrantPattern(kind: kind, pattern: pattern)
    }

    static func widenedGrantPattern(kind: String, pattern: String) -> String {
        guard kind == "READ_FS" else {
            return pattern
        }
        if pattern == "*" || pattern.hasSuffix("/*") || pattern.hasSuffix("/**") {
            return pattern
        }
        var path = pattern
        if path.hasSuffix("/") {
            path = String(path.dropLast())
        }
        let basename = (path as NSString).lastPathComponent
        if basename.contains("."), !basename.hasPrefix(".") {
            let parent = (path as NSString).deletingLastPathComponent
            if !parent.isEmpty {
                path = parent
            }
        }
        return path + "/*"
    }

    var isWebSearchGrant: Bool {
        grantKind == "WEB_FETCH" && (guiGrantPattern() ?? grantPattern) == "*"
    }

    var prefersSessionGrantFromGUI: Bool {
        ["READ_FS", "WEB_FETCH"].contains(grantKind ?? "") && !args.contains("--destructive")
    }
}

struct ToolOutcome: Identifiable, Hashable {
    let id = UUID()
    let decision: String
    let rule: String
    let reason: String
    let error: String
    let output: String
    let toolName: String
    let approvalID: Int?
    let recoverySteps: [RecoveryStep]

    init(dictionary: [String: Any]) {
        self.decision = dictionary["decision"] as? String ?? ""
        self.rule = dictionary["rule"] as? String ?? ""
        self.reason = dictionary["reason"] as? String ?? ""
        self.error = dictionary["error"] as? String ?? ""
        self.output = dictionary["output"].map { "\($0)" } ?? ""
        self.toolName = dictionary["tool_name"] as? String ?? ""
        if let rawID = dictionary["approval_id"] as? Int {
            self.approvalID = rawID
        } else if let rawID = dictionary["approval_id"] as? NSNumber {
            self.approvalID = rawID.intValue
        } else {
            self.approvalID = nil
        }
        self.recoverySteps = (dictionary["recovery_steps"] as? [[String: Any]] ?? [])
            .map(RecoveryStep.init(dictionary:))
    }

    var grantRecoveryStep: RecoveryStep? {
        recoverySteps.first { $0.command == "/grant" && $0.grantKind != nil && $0.grantPattern != nil }
    }
}

struct ApprovalDetail: Hashable {
    let approval: Approval
    let reviewArtifact: ReviewArtifact?
    let effectText: String
    let plainPolicyReason: String
    let siblingGroupID: String
    let siblingPendingCount: Int
    let siblingApprovable: Bool
    let suggestedActions: [SuggestedApprovalAction]

    init(dictionary: [String: Any]) {
        self.approval = Approval(dictionary: dictionary["approval"] as? [String: Any] ?? [:])
        if let artifactDictionary = dictionary["review_artifact"] as? [String: Any] {
            self.reviewArtifact = ReviewArtifact(dictionary: artifactDictionary)
        } else {
            self.reviewArtifact = nil
        }
        self.effectText = dictionary["effect_text"] as? String ?? ""
        self.plainPolicyReason = dictionary["plain_policy_reason"] as? String ?? ""
        let sibling = dictionary["sibling_group"] as? [String: Any] ?? [:]
        self.siblingGroupID = sibling["id"] as? String ?? ""
        self.siblingPendingCount = sibling["pending_count"] as? Int ?? 0
        self.siblingApprovable = sibling["approvable"] as? Bool ?? false
        self.suggestedActions = (dictionary["suggested_actions"] as? [[String: Any]] ?? [])
            .map(SuggestedApprovalAction.init(dictionary:))
    }
}

struct ReviewArtifact: Identifiable, Hashable {
    let id: String
    let artifactType: String
    let title: String
    let target: String
    let destinationID: String
    let effect: String
    let contentType: String
    let sha256: String
    let labels: [String]
    let preview: String
    let previewTruncated: Bool

    init(dictionary: [String: Any]) {
        self.id = dictionary["artifact_id"] as? String ?? dictionary["sha256"] as? String ?? UUID().uuidString
        self.artifactType = dictionary["artifact_type"] as? String ?? ""
        self.title = dictionary["title"] as? String ?? ""
        self.target = dictionary["target"] as? String ?? ""
        self.destinationID = dictionary["destination_id"] as? String ?? ""
        self.effect = dictionary["effect"] as? String ?? ""
        self.contentType = dictionary["content_type"] as? String ?? ""
        self.sha256 = dictionary["sha256"] as? String ?? ""
        if let labelState = dictionary["labels"] as? [String: Any] {
            self.labels = CapDepSession.flattenLabels(labelState)
        } else {
            self.labels = []
        }
        self.preview = dictionary["preview"] as? String ?? ""
        self.previewTruncated = dictionary["preview_truncated"] as? Bool ?? false
    }

    var shortHash: String {
        sha256.isEmpty ? "" : String(sha256.prefix(12))
    }

    var displayKind: String {
        artifactType.replacingOccurrences(of: "_", with: " ").capitalized
    }

    var systemImage: String {
        switch artifactType {
        case "email_draft": "envelope"
        case "calendar_event": "calendar"
        case "diff": "plus.forwardslash.minus"
        case "document": "doc.text"
        case "research": "doc.text.magnifyingglass"
        case "image": "photo"
        case "chart": "chart.xyaxis.line"
        case "script": "chevron.left.forwardslash.chevron.right"
        case "script_run": "terminal"
        case "file_export": "doc.badge.arrow.up"
        default: "doc.richtext"
        }
    }
}

struct SuggestedApprovalAction: Identifiable, Hashable {
    let id: String
    let title: String
    let detail: String

    init(dictionary: [String: Any]) {
        self.id = dictionary["id"] as? String ?? UUID().uuidString
        self.title = dictionary["title"] as? String ?? ""
        self.detail = dictionary["detail"] as? String ?? ""
    }
}

struct AppStatus: Hashable {
    let version: String
    let toolCount: Int
    let sessionCount: Int
    let activeSessionCount: Int
    let pendingApprovalCount: Int
    let auditPath: String
    let modelPlanner: String
    let modelQuarantined: String
    let localModelAvailable: Bool
    let upstreamServers: [UpstreamServerStatus]
    let capabilitiesByKind: [String: Int]

    init(dictionary: [String: Any]) {
        self.version = dictionary["version"] as? String ?? ""
        let daemon = dictionary["daemon"] as? [String: Any] ?? [:]
        self.toolCount = daemon["tool_count"] as? Int ?? 0
        self.sessionCount = daemon["session_count"] as? Int ?? 0
        self.activeSessionCount = daemon["active_session_count"] as? Int ?? 0
        self.pendingApprovalCount = daemon["pending_approval_count"] as? Int ?? 0
        self.auditPath = daemon["audit_path"] as? String ?? ""
        let model = dictionary["model"] as? [String: Any] ?? [:]
        self.modelPlanner = model["planner"] as? String ?? ""
        self.modelQuarantined = model["quarantined"] as? String ?? ""
        self.localModelAvailable = model["local_available"] as? Bool ?? false
        self.upstreamServers = (dictionary["upstream_servers"] as? [[String: Any]] ?? [])
            .map(UpstreamServerStatus.init(dictionary:))
        self.capabilitiesByKind = dictionary["capabilities_by_kind"] as? [String: Int] ?? [:]
    }

    static let empty = AppStatus(dictionary: [:])
}

struct UpstreamServerStatus: Identifiable, Hashable {
    let id: String
    let name: String
    let state: String
    let registeredToolCount: Int
    let rejectedToolCount: Int
    let error: String
    let transport: String
    let url: String

    init(dictionary: [String: Any]) {
        self.name = dictionary["name"] as? String ?? ""
        self.id = name
        self.state = dictionary["state"] as? String ?? ""
        self.registeredToolCount = dictionary["registered_tool_count"] as? Int ?? 0
        self.rejectedToolCount = dictionary["rejected_tool_count"] as? Int ?? 0
        self.error = dictionary["error"] as? String ?? ""
        self.transport = dictionary["transport"] as? String ?? ""
        self.url = dictionary["url"] as? String ?? ""
    }
}

struct SetupPlan: Hashable {
    let ready: Bool
    let workflowReady: Bool
    let firstWorkflowID: String
    let firstWorkflowTitle: String
    let firstWorkflowHint: String
    let steps: [SetupPlanStep]

    init(dictionary: [String: Any]) {
        self.ready = dictionary["ready"] as? Bool ?? false
        self.workflowReady = dictionary["workflow_ready"] as? Bool ?? false
        let workflow = dictionary["first_workflow"] as? [String: Any] ?? [:]
        self.firstWorkflowID = workflow["id"] as? String ?? ""
        self.firstWorkflowTitle = workflow["title"] as? String ?? ""
        self.firstWorkflowHint = workflow["hint"] as? String ?? ""
        self.steps = (dictionary["steps"] as? [[String: Any]] ?? [])
            .map(SetupPlanStep.init(dictionary:))
    }
}

struct SetupPlanStep: Identifiable, Hashable {
    let id: String
    let order: Int
    let title: String
    let status: String
    let detail: String
    let blocking: Bool

    init(dictionary: [String: Any]) {
        self.id = dictionary["id"] as? String ?? UUID().uuidString
        self.order = dictionary["order"] as? Int ?? 0
        self.title = dictionary["title"] as? String ?? ""
        self.status = dictionary["status"] as? String ?? ""
        self.detail = dictionary["detail"] as? String ?? ""
        self.blocking = dictionary["blocking"] as? Bool ?? false
    }
}

struct SetupCheck: Identifiable, Hashable {
    let id: String
    let title: String
    let status: String
    let detail: String
    let actions: [SetupAction]

    init(dictionary: [String: Any]) {
        self.id = dictionary["id"] as? String ?? UUID().uuidString
        self.title = dictionary["title"] as? String ?? ""
        self.status = dictionary["status"] as? String ?? ""
        self.detail = dictionary["detail"] as? String ?? ""
        self.actions = (dictionary["actions"] as? [[String: Any]] ?? [])
            .map(SetupAction.init(dictionary:))
    }

    var systemImage: String {
        switch id {
        case "daemon":
            return "server.rack"
        case "model":
            return "cpu"
        case "google-oauth":
            return "person.crop.circle.badge.checkmark"
        case "relationship-groups":
            return "person.2"
        case "approval-patterns":
            return "checklist.checked"
        case "apple-automation":
            return "macwindow"
        case "notifications":
            return "bell"
        default:
            return "checkmark.shield"
        }
    }
}

struct SetupAction: Identifiable, Hashable {
    let id: String
    let label: String
    let kind: String
    let enabled: Bool

    init(dictionary: [String: Any]) {
        self.id = dictionary["id"] as? String ?? UUID().uuidString
        self.label = dictionary["label"] as? String ?? ""
        self.kind = dictionary["kind"] as? String ?? ""
        self.enabled = dictionary["enabled"] as? Bool ?? true
    }

    var displayLabel: String {
        label.isEmpty ? "Run Action" : label
    }
}

struct ConnectorStatus: Identifiable, Hashable {
    let id: String
    let name: String
    let type: String
    let status: String
    let detail: String
    let actions: [SetupAction]

    init(dictionary: [String: Any]) {
        self.id = dictionary["id"] as? String ?? UUID().uuidString
        self.name = dictionary["name"] as? String ?? ""
        self.type = dictionary["type"] as? String ?? ""
        self.status = dictionary["status"] as? String ?? ""
        self.detail = dictionary["detail"] as? String ?? ""
        self.actions = (dictionary["actions"] as? [[String: Any]] ?? [])
            .map(SetupAction.init(dictionary:))
    }
}

struct RuntimeControlState: Hashable {
    let automationPaused: Bool
    let screenControlRequested: Bool
    let screenControlSessionID: String

    init(dictionary: [String: Any]) {
        self.automationPaused = dictionary["automation_paused"] as? Bool ?? false
        self.screenControlRequested = dictionary["screen_control_requested"] as? Bool ?? false
        self.screenControlSessionID = dictionary["screen_control_session_id"] as? String ?? ""
    }

    static let empty = RuntimeControlState(dictionary: [:])
}

struct SourceBindingViewData: Identifiable, Hashable {
    let id: String
    let name: String
    let scopePatternCanonical: String
    let category: String
    let defaultTier: String
    let writeDiscipline: String
    let riskIDs: [String]

    init(dictionary: [String: Any]) {
        self.name = dictionary["name"] as? String ?? ""
        self.id = name
        self.scopePatternCanonical = dictionary["scope_pattern_canonical"] as? String ?? ""
        self.category = dictionary["category"] as? String ?? ""
        self.defaultTier = dictionary["default_tier"] as? String ?? ""
        self.writeDiscipline = dictionary["write_discipline"] as? String ?? ""
        self.riskIDs = dictionary["risk_ids"] as? [String] ?? []
    }
}

struct GmailOAuthStatus: Hashable {
    let serviceID: String
    let displayName: String
    let configured: Bool
    let clientIDConfigured: Bool
    let clientSecretConfigured: Bool
    let tokenConfigured: Bool
    let serverYAML: String
    let clientIDFile: String
    let clientSecretFile: String
    let tokenCache: String
    let restartRequired: Bool

    init(dictionary: [String: Any]) {
        self.serviceID = dictionary["service_id"] as? String
            ?? dictionary["server"] as? String
            ?? "google-gmail"
        self.displayName = dictionary["display_name"] as? String ?? "Google Gmail"
        self.configured = dictionary["configured"] as? Bool ?? false
        self.clientIDConfigured = dictionary["client_id_configured"] as? Bool ?? false
        self.clientSecretConfigured = dictionary["client_secret_configured"] as? Bool ?? false
        self.tokenConfigured = dictionary["token_configured"] as? Bool ?? false
        self.serverYAML = dictionary["server_yaml"] as? String ?? ""
        self.clientIDFile = dictionary["client_id_file"] as? String ?? ""
        self.clientSecretFile = dictionary["client_secret_file"] as? String ?? ""
        self.tokenCache = dictionary["token_cache"] as? String ?? ""
        self.restartRequired = dictionary["restart_required"] as? Bool ?? false
    }

    static let empty = GmailOAuthStatus(dictionary: [:])
}

typealias GoogleOAuthStatus = GmailOAuthStatus

struct GoogleOAuthPreset: Identifiable, Hashable {
    let id: String
    let displayName: String
    let description: String
    let serviceIDs: [String]
    let grantsSummary: String
    let configuredCount: Int
    let connectedCount: Int
    let totalCount: Int
    let connected: Bool
    let nextServiceID: String

    init(dictionary: [String: Any]) {
        self.id = dictionary["id"] as? String ?? ""
        self.displayName = dictionary["display_name"] as? String ?? id
        self.description = dictionary["description"] as? String ?? ""
        self.serviceIDs = dictionary["service_ids"] as? [String] ?? []
        self.grantsSummary = dictionary["grants_summary"] as? String ?? ""
        self.configuredCount = dictionary["configured_count"] as? Int ?? 0
        self.connectedCount = dictionary["connected_count"] as? Int ?? 0
        self.totalCount = dictionary["total_count"] as? Int ?? serviceIDs.count
        self.connected = dictionary["connected"] as? Bool ?? false
        self.nextServiceID = dictionary["next_service_id"] as? String ?? serviceIDs.first ?? "google-gmail"
    }
}

struct DaemonSettings: Hashable {
    var defaultPurpose: String
    var globalShortcut: String
    var imageProfile: String
    var launchAtLogin: Bool
    var notificationsEnabled: Bool
    var preferLocalMLX: Bool
    var showThinkingOutput: Bool
    var enableScreenControl: Bool
    var requireTouchIDForHighRisk: Bool
    var verboseDaemonLogging: Bool

    init(dictionary: [String: Any]) {
        self.defaultPurpose = dictionary["default_purpose"] as? String ?? "general"
        self.globalShortcut = dictionary["global_shortcut"] as? String ?? "Option-Space"
        self.imageProfile = dictionary["image_profile"] as? String ?? "default"
        self.launchAtLogin = dictionary["launch_at_login"] as? Bool ?? false
        self.notificationsEnabled = dictionary["notifications_enabled"] as? Bool ?? true
        self.preferLocalMLX = dictionary["prefer_local_mlx"] as? Bool ?? true
        self.showThinkingOutput = dictionary["show_thinking_output"] as? Bool ?? false
        self.enableScreenControl = dictionary["enable_screen_control"] as? Bool ?? false
        self.requireTouchIDForHighRisk = dictionary["require_touch_id_for_high_risk"] as? Bool ?? false
        self.verboseDaemonLogging = dictionary["verbose_daemon_logging"] as? Bool ?? false
    }

    var rpcDictionary: [String: Any] {
        [
            "default_purpose": defaultPurpose,
            "global_shortcut": globalShortcut,
            "image_profile": imageProfile,
            "launch_at_login": launchAtLogin,
            "notifications_enabled": notificationsEnabled,
            "prefer_local_mlx": preferLocalMLX,
            "show_thinking_output": showThinkingOutput,
            "enable_screen_control": enableScreenControl,
            "require_touch_id_for_high_risk": requireTouchIDForHighRisk,
            "verbose_daemon_logging": verboseDaemonLogging,
        ]
    }

    static let empty = DaemonSettings(dictionary: [:])
}

struct ImageProfile: Identifiable, Hashable {
    let id: String
    let title: String
    let tier: String
    let detail: String
    let backend: String
    let model: String
    let steps: Int
    let recommended: Bool
    let slow: Bool

    init(dictionary: [String: Any]) {
        self.id = dictionary["id"] as? String ?? ""
        self.title = dictionary["title"] as? String ?? id
        self.tier = dictionary["tier"] as? String ?? ""
        self.detail = dictionary["description"] as? String ?? ""
        self.backend = dictionary["backend"] as? String ?? ""
        self.model = dictionary["model"] as? String ?? ""
        self.steps = dictionary["steps"] as? Int ?? 0
        self.recommended = dictionary["recommended"] as? Bool ?? false
        self.slow = dictionary["slow"] as? Bool ?? false
    }

    var displayTitle: String {
        if recommended {
            return "\(title) (recommended)"
        }
        if slow {
            return "\(title) (slow)"
        }
        return title
    }
}

struct ImageReadinessCheck: Identifiable, Hashable {
    let id: String
    let status: String
    let detail: String
    let recovery: String

    init(dictionary: [String: Any]) {
        self.id = dictionary["id"] as? String ?? UUID().uuidString
        self.status = dictionary["status"] as? String ?? "unknown"
        self.detail = dictionary["detail"] as? String ?? ""
        self.recovery = dictionary["recovery"] as? String ?? ""
    }
}

struct ImageReadiness: Hashable {
    let ok: Bool
    let profile: String
    let backend: String
    let model: String
    let modelPath: String
    let device: String
    let checks: [ImageReadinessCheck]

    init(dictionary: [String: Any]) {
        self.ok = dictionary["ok"] as? Bool ?? false
        self.profile = dictionary["profile"] as? String ?? ""
        self.backend = dictionary["backend"] as? String ?? ""
        self.model = dictionary["model"] as? String ?? ""
        self.modelPath = dictionary["model_path"] as? String ?? ""
        self.device = dictionary["device"] as? String ?? ""
        self.checks = (dictionary["checks"] as? [[String: Any]] ?? [])
            .map(ImageReadinessCheck.init(dictionary:))
    }

    static let empty = ImageReadiness(dictionary: [:])
}

struct ConfigValidation: Hashable {
    let ok: Bool
    let configPath: String
    let issues: [ConfigValidationIssue]

    init(dictionary: [String: Any]) {
        self.ok = dictionary["ok"] as? Bool ?? false
        self.configPath = dictionary["config_path"] as? String ?? ""
        self.issues = (dictionary["issues"] as? [[String: Any]] ?? [])
            .map(ConfigValidationIssue.init(dictionary:))
    }

    static let empty = ConfigValidation(dictionary: [:])
}

struct ConfigValidationIssue: Identifiable, Hashable {
    let id = UUID()
    let severity: String
    let subject: String
    let message: String

    init(dictionary: [String: Any]) {
        self.severity = dictionary["severity"] as? String ?? ""
        self.subject = dictionary["subject"] as? String ?? ""
        self.message = dictionary["message"] as? String ?? ""
    }
}

struct LogLocation: Identifiable, Hashable {
    let id: String
    let title: String
    let path: String

    init(dictionary: [String: Any]) {
        self.id = dictionary["id"] as? String ?? UUID().uuidString
        self.title = dictionary["title"] as? String ?? ""
        self.path = dictionary["path"] as? String ?? ""
    }
}

struct ProvenanceNode: Identifiable, Hashable {
    let id: String
    let kind: String
    let materializedID: String

    init(dictionary: [String: Any]) {
        self.id = dictionary["id"] as? String ?? ""
        self.kind = dictionary["kind"] as? String ?? ""
        self.materializedID = dictionary["materialized_id"] as? String ?? ""
    }
}

struct ProvenanceEdge: Identifiable, Hashable {
    let id = UUID()
    let from: String
    let to: String
    let kind: String

    init(dictionary: [String: Any]) {
        self.from = dictionary["from"] as? String ?? ""
        self.to = dictionary["to"] as? String ?? ""
        self.kind = dictionary["kind"] as? String ?? ""
    }
}

struct SessionSecurityContext: Hashable {
    let sessionID: String
    let enforcementMode: String
    let labelCount: Int
    let activeCapabilityCount: Int
    let usedKinds: [String]
    let pendingApprovalCount: Int
    let policyDecisionCount: Int
    let policyDenyCount: Int
    let matchedRuleIDs: [String]
    let provenanceNodeCount: Int
    let provenanceEdgeCount: Int
    let externalMCPActors: [String]
    let toolActors: [String]
    let onguardClientID: String
    let securityModels: [SecurityContextItem]
    let flowPatterns: [SecurityContextItem]
    let limitations: [String]

    init(dictionary: [String: Any]) {
        let session = dictionary["session"] as? [String: Any] ?? [:]
        let labels = dictionary["labels"] as? [String: Any] ?? [:]
        let capabilities = dictionary["capabilities"] as? [String: Any] ?? [:]
        let approvals = dictionary["approvals"] as? [String: Any] ?? [:]
        let policy = dictionary["policy"] as? [String: Any] ?? [:]
        let provenance = dictionary["provenance"] as? [String: Any] ?? [:]
        let actors = dictionary["actors"] as? [String: Any] ?? [:]
        let onguard = actors["onguard"] as? [String: Any] ?? [:]
        let onguardClient = onguard["client"] as? [String: Any] ?? [:]

        self.sessionID = session["id"] as? String ?? ""
        self.enforcementMode = session["enforcement_mode"] as? String ?? ""
        self.labelCount = CapDepSession.flattenLabels(
            labels["label_state"] as? [String: Any] ?? [:],
        ).count
        self.activeCapabilityCount = (capabilities["active"] as? [[String: Any]] ?? []).count
        self.usedKinds = capabilities["used_kinds"] as? [String] ?? []
        self.pendingApprovalCount = approvals["pending_count"] as? Int ?? 0
        self.policyDecisionCount = policy["decision_count"] as? Int ?? 0
        self.policyDenyCount = policy["deny_count"] as? Int ?? 0
        self.matchedRuleIDs = policy["matched_rule_ids"] as? [String] ?? []
        self.provenanceNodeCount = provenance["node_count"] as? Int ?? 0
        self.provenanceEdgeCount = provenance["edge_count"] as? Int ?? 0
        self.externalMCPActors = (actors["external_mcp"] as? [[String: Any]] ?? [])
            .compactMap { $0["name"] as? String }
            .sorted()
        self.toolActors = (actors["tools"] as? [[String: Any]] ?? [])
            .compactMap { $0["name"] as? String }
            .sorted()
        self.onguardClientID = onguardClient["client_id"] as? String ?? ""
        self.securityModels = (dictionary["security_models"] as? [[String: Any]] ?? [])
            .map(SecurityContextItem.init(dictionary:))
        self.flowPatterns = (dictionary["flow_patterns"] as? [[String: Any]] ?? [])
            .map(SecurityContextItem.init(dictionary:))
        self.limitations = dictionary["limitations"] as? [String] ?? []
    }

    static let empty = SessionSecurityContext(dictionary: [:])
}

struct SecurityContextItem: Identifiable, Hashable {
    let id: String
    let name: String
    let active: Bool
    let evidenceSummary: String

    init(dictionary: [String: Any]) {
        self.name = dictionary["name"] as? String ?? ""
        self.id = name
        if let implemented = dictionary["implemented"] as? Bool {
            self.active = implemented
        } else {
            self.active = dictionary["active"] as? Bool ?? false
        }
        let evidence = dictionary["evidence"] as? [String: Any] ?? [:]
        self.evidenceSummary = evidence
            .sorted { $0.key < $1.key }
            .prefix(5)
            .map { "\($0.key)=\($0.value)" }
            .joined(separator: "  ")
    }
}

struct RelationshipGroupViewData: Identifiable, Hashable {
    let id: String
    let members: [String]

    init(dictionary: [String: Any]) {
        self.id = dictionary["group_id"] as? String ?? ""
        self.members = dictionary["member_principal_ids"] as? [String] ?? []
    }
}

struct ApprovalPatternViewData: Identifiable, Hashable {
    let id: String
    let action: String
    let targetPattern: String
    let createdBy: String

    init(dictionary: [String: Any]) {
        self.id = dictionary["id"] as? String ?? UUID().uuidString
        self.action = dictionary["action"] as? String ?? ""
        self.targetPattern = dictionary["target_pattern"] as? String ?? ""
        self.createdBy = dictionary["created_by"] as? String ?? ""
    }
}

struct MemoryEntryViewData: Identifiable, Hashable {
    let id: String
    let key: String
    let labels: [String]

    init(dictionary: [String: Any]) {
        self.key = dictionary["key"] as? String ?? ""
        self.id = key
        self.labels = dictionary["labels"] as? [String] ?? []
    }
}

struct DaemonToolViewData: Identifiable, Hashable {
    let id: String
    let name: String
    let description: String
    let capabilityKind: String
    let targetArg: String

    init(dictionary: [String: Any]) {
        self.name = dictionary["name"] as? String ?? ""
        self.id = name
        self.description = dictionary["description"] as? String ?? ""
        self.capabilityKind = dictionary["capability_kind"] as? String ?? ""
        self.targetArg = dictionary["target_arg"] as? String ?? ""
    }
}

struct OverrideGrantViewData: Identifiable, Hashable {
    let id: String
    let sessionID: String
    let actionKind: String
    let target: String
    let state: String
    let expiresAt: String

    init(dictionary: [String: Any]) {
        self.id = dictionary["id"] as? String ?? UUID().uuidString
        self.sessionID = dictionary["session_id"] as? String ?? ""
        self.actionKind = dictionary["action_kind"] as? String ?? ""
        self.target = dictionary["target"] as? String ?? ""
        self.state = dictionary["state"] as? String ?? ""
        self.expiresAt = dictionary["expires_at"] as? String ?? ""
    }
}

struct OnguardClientViewData: Identifiable, Hashable {
    let id: String
    let kind: String
    let status: String
    let owner: String

    init(dictionary: [String: Any]) {
        self.id = dictionary["client_id"] as? String ?? UUID().uuidString
        self.kind = dictionary["kind"] as? String ?? ""
        self.status = dictionary["status"] as? String ?? ""
        self.owner = dictionary["owner"] as? String ?? ""
    }
}

struct OnguardCommandViewData: Identifiable, Hashable {
    let id: String
    let clientID: String
    let command: String
    let status: String
    let labels: [String]

    init(dictionary: [String: Any]) {
        self.id = dictionary["command_id"] as? String ?? UUID().uuidString
        self.clientID = dictionary["client_id"] as? String ?? ""
        self.command = dictionary["command"] as? String ?? ""
        self.status = dictionary["status"] as? String ?? ""
        self.labels = dictionary["labels"] as? [String] ?? []
    }
}

struct OnguardScheduleViewData: Identifiable, Hashable {
    let id: String
    let clientID: String
    let command: String
    let status: String
    let nextRunAt: String

    init(dictionary: [String: Any]) {
        self.id = dictionary["schedule_id"] as? String ?? UUID().uuidString
        self.clientID = dictionary["client_id"] as? String ?? ""
        self.command = dictionary["command"] as? String ?? ""
        self.status = dictionary["status"] as? String ?? ""
        self.nextRunAt = dictionary["next_run_at"] as? String ?? ""
    }
}

struct OnguardArtifactViewData: Identifiable, Hashable {
    let id: String
    let clientID: String
    let artifactType: String
    let status: String
    let labels: [String]

    init(dictionary: [String: Any]) {
        self.id = dictionary["artifact_id"] as? String ?? UUID().uuidString
        self.clientID = dictionary["client_id"] as? String ?? ""
        self.artifactType = dictionary["artifact_type"] as? String ?? ""
        self.status = dictionary["status"] as? String ?? ""
        self.labels = dictionary["labels"] as? [String] ?? []
    }
}

struct OnguardEventViewData: Identifiable, Hashable {
    let id: String
    let clientID: String
    let eventType: String
    let acknowledgedBy: String

    init(dictionary: [String: Any]) {
        self.id = dictionary["event_id"] as? String ?? UUID().uuidString
        self.clientID = dictionary["client_id"] as? String ?? ""
        self.eventType = dictionary["event_type"] as? String ?? ""
        self.acknowledgedBy = dictionary["acknowledged_by"] as? String ?? ""
    }
}

struct OnguardNotificationViewData: Identifiable, Hashable {
    let id: String
    let notificationClass: String
    let urgency: String
    let title: String
    let body: String
    let deepLink: String
    let artifactRef: String?
    let approvalID: Int?

    init(dictionary: [String: Any]) {
        self.id = dictionary["id"] as? String ?? UUID().uuidString
        self.notificationClass = dictionary["class"] as? String ?? ""
        self.urgency = dictionary["urgency"] as? String ?? ""
        self.title = dictionary["title"] as? String ?? ""
        self.body = dictionary["body"] as? String ?? ""
        self.deepLink = dictionary["deep_link"] as? String ?? ""
        self.artifactRef = dictionary["artifact_ref"] as? String
        self.approvalID = dictionary["approval_id"] as? Int
    }
}

struct OnguardConfigViewData: Identifiable, Hashable {
    let id: String
    let clientID: String
    let schemaName: String
    let status: String
    let labels: [String]

    init(dictionary: [String: Any]) {
        self.id = dictionary["config_id"] as? String ?? UUID().uuidString
        self.clientID = dictionary["client_id"] as? String ?? ""
        self.schemaName = dictionary["schema_name"] as? String ?? ""
        self.status = dictionary["status"] as? String ?? ""
        self.labels = dictionary["labels"] as? [String] ?? []
    }
}
