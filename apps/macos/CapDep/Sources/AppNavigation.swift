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

enum Purpose: String, CaseIterable, Identifiable {
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
    let systemImage: String
    let requiresForegroundReview: Bool
}

struct ContextChip: Identifiable, Hashable {
    let id = UUID()
    let title: String
    let detail: String
    let kind: String
    let isSensitive: Bool
    let isUntrusted: Bool
}

let defaultWorkflowTemplates: [WorkflowTemplate] = [
    WorkflowTemplate(
        id: "morning-briefing",
        title: "Morning Briefing",
        subtitle: "Calendar, inbox, notes, conflicts, and action items.",
        purpose: .general,
        prompt: "Prepare my morning briefing with calendar conflicts, urgent mail, and action items.",
        systemImage: "sunrise",
        requiresForegroundReview: false,
    ),
    WorkflowTemplate(
        id: "inbox-triage",
        title: "Inbox Triage",
        subtitle: "Summarize and classify messages; draft replies without sending.",
        purpose: .inbox,
        prompt: "Triage my inbox and prepare drafts for messages that need replies.",
        systemImage: "tray.full",
        requiresForegroundReview: false,
    ),
    WorkflowTemplate(
        id: "calendar-planning",
        title: "Calendar Planning",
        subtitle: "Find time, explain conflicts, and propose event changes.",
        purpose: .calendar,
        prompt: "Review my calendar, find scheduling conflicts, and propose safe calendar changes.",
        systemImage: "calendar.badge.clock",
        requiresForegroundReview: true,
    ),
    WorkflowTemplate(
        id: "web-research",
        title: "Web Research",
        subtitle: "Research and synthesize external sources as untrusted input.",
        purpose: .research,
        prompt: "Research this topic, keep web sources labeled as untrusted, and produce a cited summary.",
        systemImage: "safari",
        requiresForegroundReview: false,
    ),
    WorkflowTemplate(
        id: "summarize-selection",
        title: "Summarize Selection",
        subtitle: "Use selected text, files, or current app context.",
        purpose: .general,
        prompt: "Summarize the current selection and list any action items.",
        systemImage: "selection.pin.in.out",
        requiresForegroundReview: false,
    ),
    WorkflowTemplate(
        id: "revise-document",
        title: "Revise Frontmost Document",
        subtitle: "Prepare bounded edits for Pages, Numbers, or Keynote.",
        purpose: .writing,
        prompt: "Review the frontmost document and suggest bounded edits before applying anything.",
        systemImage: "doc.text.magnifyingglass",
        requiresForegroundReview: true,
    ),
]
