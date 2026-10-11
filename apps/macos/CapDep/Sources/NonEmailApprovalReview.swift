import SwiftUI

/// What the task review shows for an approval that has no dedicated native
/// review yet (purchases, destructive actions, access grants, information
/// release).
///
/// Before this, the screen said only "needs its specialized review" beside a
/// permanently disabled primary button: the user could neither see what was
/// being asked nor learn where to decide it. This surfaces the exact facts the
/// daemon holds and names the real terminal route. It deliberately offers no
/// approve button: deciding needs the full payload review that only the
/// specialised surfaces provide.
struct NonEmailApprovalReview: Equatable {
    struct Fact: Equatable, Identifiable {
        let label: String
        let value: String
        var id: String { label }
    }

    let id: Int
    let heading: String
    let facts: [Fact]

    init(approval: Approval) {
        id = approval.id
        heading = Self.plainAction(approval.action)
        var facts = [Fact(label: "What", value: Self.plainAction(approval.action))]
        Self.append(&facts, "Target", approval.target)
        Self.append(&facts, "Why it was requested", approval.justification)
        if !approval.labelsIn.isEmpty {
            facts.append(Fact(label: "Information involved", value: approval.labelsIn.joined(separator: ", ")))
        }
        Self.append(&facts, "Review expires", approval.expiresAt)
        self.facts = facts
    }

    /// Why this screen cannot approve, and where the user can decide instead.
    var explanation: String {
        "This Mac app can't approve this kind of action yet, so nothing will happen unless you decide it elsewhere. You can still choose Don't proceed here."
    }

    /// Real, copyable terminal commands for this exact review.
    var routeCommands: [String] {
        [
            "capdep approval show \(id)",
            "capdep approval approve \(id)",
            "capdep approval deny \(id)",
        ]
    }

    private static func append(_ facts: inout [Fact], _ label: String, _ value: String) {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        if !trimmed.isEmpty { facts.append(Fact(label: label, value: trimmed)) }
    }

    static func plainAction(_ action: String) -> String {
        switch action {
        case "QUEUE_PURCHASE": "Spend money"
        case "EXECUTE_DESTRUCTIVE": "Delete or change data"
        case "GRANT": "Grant access"
        case "DECLASSIFY": "Release information"
        default: action.isEmpty ? "Take an action" : action.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }
}

struct NonEmailApprovalReviewSection: View {
    let review: NonEmailApprovalReview

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(review.heading).font(.headline)
            ForEach(review.facts) { fact in
                VStack(alignment: .leading, spacing: 3) {
                    Text(fact.label).font(.caption).foregroundStyle(.secondary)
                    Text(fact.value).textSelection(.enabled)
                }
                .accessibilityElement(children: .combine)
            }
            Text(review.explanation).font(.callout).foregroundStyle(.secondary)
            DisclosureGroup("Decide in the terminal") {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(review.routeCommands, id: \.self) { command in
                        Text(command).font(.caption.monospaced()).textSelection(.enabled)
                    }
                }
            }
        }
        .accessibilityIdentifier("capdep.task-review.non-email")
    }
}
