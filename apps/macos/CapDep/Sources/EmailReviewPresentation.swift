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
    }

    var exactText: String {
        "Account: \(account)\nFrom: \(from)\nTo: \(to)\nSubject: \(subject)\n\n\(body)"
    }
}
