import Foundation

struct Approval: Identifiable, Hashable {
    let id: Int
    let action: String
    let status: String
    let target: String
    let fromSession: String
    let justification: String
    let payload: String
    let labelsIn: [String]
    let labelsOut: [String]

    init(dictionary: [String: Any]) {
        self.id = dictionary["id"] as? Int ?? 0
        self.action = dictionary["action"] as? String ?? ""
        self.status = dictionary["status"] as? String ?? ""
        self.target = dictionary["target"] as? String ?? ""
        self.fromSession = dictionary["from_session"] as? String ?? ""
        self.justification = dictionary["justification"] as? String ?? ""
        self.payload = dictionary["payload"] as? String ?? ""
        self.labelsIn = Self.labels(from: dictionary["labels_in"])
        self.labelsOut = Self.labels(from: dictionary["labels_out"])
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

struct SetupCheck: Identifiable, Hashable {
    let id: String
    let title: String
    let status: String
    let detail: String

    init(dictionary: [String: Any]) {
        self.id = dictionary["id"] as? String ?? UUID().uuidString
        self.title = dictionary["title"] as? String ?? ""
        self.status = dictionary["status"] as? String ?? ""
        self.detail = dictionary["detail"] as? String ?? ""
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
