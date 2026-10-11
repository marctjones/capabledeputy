import Foundation

extension CapDepAppModel {
    func loadEmailSources(sessionID: String) async throws -> EmailSourceSnapshot {
        let result = try await client.call(method: "email.source_list", params: ["session_id": sessionID])
        let raw = result as? [String: Any] ?? [:]
        return EmailSourceSnapshot(raw)
    }

    func loadEmailSourceBody(sessionID: String, sourceID: String) async throws -> String {
        let result = try await client.call(method: "email.source_list", params: ["session_id": sessionID])
        let raw = result as? [String: Any] ?? [:]
        let sources = raw["sources"] as? [[String: Any]] ?? []
        guard let source = sources.first(where: { $0["id"] as? String == sourceID }),
              let body = source["body"] as? String else {
            throw DaemonClientError.daemonError("Source message is unavailable. Reload before replying.")
        }
        return body
    }

    func createEmailReply(sessionID: String, sourceID: String) async throws -> SessionEmailDraft {
        let result = try await client.call(method: "email.reply_create", params: [
            "session_id": sessionID, "source_id": sourceID,
        ])
        return try parseEmailDraft(result)
    }

    func updateEmailDraft(sessionID: String, draft: SessionEmailDraft, to: String, subject: String, body: String) async throws -> SessionEmailDraft {
        let result = try await client.call(method: "email.draft_update", params: [
            "session_id": sessionID, "id": draft.id, "version": draft.version,
            "to": to, "subject": subject, "body": body,
        ])
        return try parseEmailDraft(result)
    }

    func loadEmailOperations(sessionID: String) async throws -> [EmailOperation] {
        let result = try await client.call(method: "email.operation_list", params: ["session_id": sessionID])
        let raw = result as? [String: Any] ?? [:]
        return (raw["operations"] as? [[String: Any]] ?? []).compactMap(EmailOperation.init)
    }

    func reconcileEmailOperation(sessionID: String, operationID: String, outcome: String, reason: String) async throws {
        _ = try await client.call(method: "email.operation_reconcile", params: [
            "session_id": sessionID, "id": operationID, "outcome": outcome,
            "reason": reason, "confirmed": true,
        ])
    }

    private func parseEmailDraft(_ result: Any) throws -> SessionEmailDraft {
        guard let raw = result as? [String: Any], let data = raw["draft"] as? [String: Any],
              let draft = SessionEmailDraft(data) else {
            throw DaemonClientError.invalidResponse
        }
        return draft
    }
}
