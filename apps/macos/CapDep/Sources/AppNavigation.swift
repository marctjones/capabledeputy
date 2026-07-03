import Foundation

enum DashboardSection: String, CaseIterable, Identifiable {
    case today = "Today"
    case approvals = "Approvals"
    case sessions = "Sessions"
    case workflows = "Workflows"
    case onguard = "Onguard"
    case policyTrace = "Policy Trace"
    case provenance = "Provenance"
    case trust = "Trust"
    case setup = "Setup"
    case daemonRPC = "Daemon RPC"
    case settings = "Settings"

    var id: String { rawValue }

    var systemImage: String {
        switch self {
        case .today: "sun.max"
        case .approvals: "hand.raised"
        case .sessions: "rectangle.connected.to.line.below"
        case .workflows: "bolt.horizontal"
        case .onguard: "clock.badge.checkmark"
        case .policyTrace: "list.bullet.rectangle"
        case .provenance: "point.3.connected.trianglepath.dotted"
        case .trust: "person.2.badge.gearshape"
        case .setup: "checklist"
        case .daemonRPC: "terminal"
        case .settings: "gearshape"
        }
    }
}

enum Purpose: String, CaseIterable, Identifiable, Codable {
    case general
    case inbox
    case calendar
    case writing
    case research

    var id: String { rawValue }
}

struct WorkflowTemplate: Identifiable, Hashable {
    let id: String
    let title: String
    let subtitle: String
    let purpose: Purpose
    let prompt: String
    let turnMessage: String
    let systemImage: String
    let requiresForegroundReview: Bool

    init(dictionary: [String: Any]) {
        self.id = dictionary["id"] as? String ?? UUID().uuidString
        self.title = dictionary["title"] as? String ?? ""
        self.subtitle = dictionary["subtitle"] as? String ?? ""
        let purposeHandle = dictionary["purpose_handle"] as? String ?? Purpose.general.rawValue
        self.purpose = Purpose(rawValue: purposeHandle) ?? .general
        self.prompt = dictionary["prompt"] as? String ?? ""
        let explicitTurnMessage = dictionary["turn_message"] as? String ?? ""
        self.turnMessage = explicitTurnMessage.isEmpty ? self.prompt : explicitTurnMessage
        self.systemImage = dictionary["system_image"] as? String ?? "bolt.horizontal"
        self.requiresForegroundReview = dictionary["requires_foreground_review"] as? Bool ?? false
    }
}

struct ContextChip: Identifiable, Hashable {
    let id = UUID()
    let title: String
    let detail: String
    let kind: String
    let isSensitive: Bool
    let isUntrusted: Bool
}

