import Foundation

/// Only a daemon approval envelope can produce this review presentation.
/// Transcript Markdown is never interpreted as an action or approval.
struct EmailReviewPresentation {
    let account: String
    let from: String
    let to: String
    let subject: String
    let body: String
    let inReplyTo: String
    let messageID: String
    let references: String
    let sourceID: String
    let version: Int?
    let draftID: String
    let isBound: Bool

    init?(payload: String) {
        guard let data = payload.data(using: .utf8),
              let raw = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let format = raw["format"] as? String,
              ["capdep.email.v1", "capdep.email.v2"].contains(format),
              let message = raw["message"] as? [String: Any],
              let to = message["to"] as? String,
              let subject = message["subject"] as? String,
              let body = message["body"] as? String else { return nil }
        self.to = to; self.subject = subject; self.body = body
        account = message["account_id"] as? String ?? "Not bound in this legacy review"
        from = message["from_addr"] as? String ?? "Not bound in this legacy review"
        inReplyTo = message["in_reply_to"] as? String ?? ""
        messageID = message["message_id"] as? String ?? ""
        references = (message["references"] as? [String])?.joined(separator: " ") ?? message["references"] as? String ?? ""
        sourceID = raw["source_id"] as? String ?? ""
        version = raw["draft_version"] as? Int
        draftID = raw["draft_id"] as? String ?? ""
        isBound = format == "capdep.email.v2" && !draftID.isEmpty && version != nil
            && Set(message.keys) == Set(["account_id", "from_addr", "to", "subject", "body", "in_reply_to", "references", "message_id"])
            && message.values.allSatisfy { $0 is String }
            && !body.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && !(message["account_id"] as? String ?? "").isEmpty
            && !(message["from_addr"] as? String ?? "").isEmpty
            && !(raw["source_id"] as? String ?? "").isEmpty
            && !(raw["account_fingerprint"] as? String ?? "").isEmpty
            && !(raw["envelope_digest"] as? String ?? "").isEmpty
    }

    var exactText: String {
        "Account: \(account)\nFrom: \(from)\nTo: \(to)\nSubject: \(subject)\n\n\(body)"
    }
}
