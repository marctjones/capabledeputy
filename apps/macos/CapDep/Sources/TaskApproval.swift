import Foundation

/// Only the daemon's typed action projection supplies these facts. Never parse chat prose.
struct TaskApproval: Identifiable {
    let approval: Approval
    let state: String
    let error: String
    let canApprove: Bool
    let operationID: String
    let operationDetail: String
    var id: Int { approval.id }
    var email: EmailReviewPresentation? { EmailReviewPresentation(payload: approval.payload) }

    init(_ raw: [String: Any]) {
        approval = Approval(dictionary: raw)
        state = raw["review_state"] as? String ?? approval.status
        error = raw["review_error"] as? String ?? ""
        canApprove = raw["can_approve"] as? Bool == true && state == "pending" && approval.status == "pending"
        let operation = raw["operation"] as? [String: Any] ?? [:]
        operationID = operation["id"] as? String ?? ""
        operationDetail = operation["detail"] as? String ?? ""
    }

    var title: String {
        switch state {
        case "pending": "Ready for your review"
        case "approved", "authorized": "Approved · Waiting for send status"
        case "submitting": "Sending reply…"
        case "smtp_accepted": "Accepted by your mail service"
        case "outcome_unknown": "Send outcome unknown"
        case "failed_before_submission", "confirmed_not_sent", "denied": "Not sent"
        case "expired": "Review expired"
        case "superseded": "Review replaced"
        case "changed": "Draft changed"
        case "deferred": "Review deferred"
        default: "Check action status"
        }
    }

    var explanation: String {
        switch state {
        case "pending": "Nothing has been sent."
        case "smtp_accepted": "Your mail service accepted this reply. Recipient delivery isn't confirmed."
        case "outcome_unknown": "The mail service may have received this reply. Check its status before considering another send."
        case "failed_before_submission", "confirmed_not_sent": "The reply was not submitted. Prepare a fresh review before trying again."
        case "denied": "You chose not to send this reply. Your draft is still available."
        case "expired": "Prepare a fresh review before sending. This review can no longer authorize a send."
        case "superseded", "changed": "This saved review can no longer be sent. Review the current draft before sending."
        case "approved", "authorized", "submitting": "Your decision was recorded. The send result has not been confirmed."
        default: "Check the current state before taking another action."
        }
    }

    var permitsFreshReview: Bool {
        ["expired", "changed", "superseded", "denied", "deferred", "failed_before_submission", "confirmed_not_sent"].contains(state)
    }
}

extension Approval {
    var actionTitle: String {
        switch action {
        case "SEND_EMAIL": "Reply to \(EmailReviewPresentation(payload: payload)?.to ?? target)"
        case "QUEUE_PURCHASE": "Review purchase"
        case "EXECUTE_DESTRUCTIVE": "Review destructive action"
        case "GRANT": "Review access request"
        case "DECLASSIFY": "Review information release"
        default: "Review requested action"
        }
    }
}

/// Window closure keeps unsaved edits in the app model; daemon consent stays withdrawn.
struct ReplyEditorState {
    let approvalID: Int
    let draft: SessionEmailDraft
    var to: String
    var subject: String
    var body: String
}

extension CapDepAppModel {
    func changedReplyFields(_ item: TaskApproval) -> String {
        guard let current = item.email, !current.draftID.isEmpty,
              let previous = taskApprovals[item.approval.fromSession]?.filter({
                  $0.id < item.id && $0.email?.draftID == current.draftID
              }).max(by: { $0.id < $1.id })?.email else { return "" }
        let fields = [("sending account", current.account != previous.account || current.from != previous.from),
                      ("recipient", current.to != previous.to), ("subject", current.subject != previous.subject),
                      ("body", current.body != previous.body)].filter { $0.1 }.map { $0.0 }
        return fields.isEmpty ? "" : "Changed since the previous review: " + fields.joined(separator: ", ") + "."
    }

    func visibleTaskApprovals(sessionID: String) -> [TaskApproval] {
        let rows = taskApprovals[sessionID] ?? []
        var latest: [String: TaskApproval] = [:]
        for row in rows.sorted(by: { $0.id < $1.id }) {
            let key = row.email.flatMap { $0.draftID.isEmpty ? nil : "draft:\($0.draftID)" } ?? "action:\(row.id)"
            latest[key] = row
        }
        return latest.values.sorted { $0.id < $1.id }
    }
    func refreshTaskApprovals(sessionID: String) async {
        do {
            let response = try await client.call(method: "approval.task_list", params: ["session_id": sessionID]) as? [String: Any]
            taskApprovals[sessionID] = (response?["reviews"] as? [[String: Any]] ?? []).map(TaskApproval.init)
            taskApprovalErrors[sessionID] = nil
        } catch {
            taskApprovalErrors[sessionID] = error.localizedDescription
        }
    }

    func beginEditingReply(_ item: TaskApproval) async throws -> SessionEmailDraft {
        guard let email = item.email, let version = email.version else { throw DaemonClientError.invalidResponse }
        let result = try await client.call(method: "email.draft_begin_edit", params: [
            "session_id": item.approval.fromSession, "id": email.draftID, "version": version,
        ]) as? [String: Any]
        guard let raw = result?["draft"] as? [String: Any], let draft = SessionEmailDraft(raw) else { throw DaemonClientError.invalidResponse }
        await refreshTaskApprovals(sessionID: item.approval.fromSession)
        return draft
    }

    func freshReplyReview(_ item: TaskApproval) async throws {
        guard let email = item.email else { throw DaemonClientError.invalidResponse }
        let result = try await client.call(method: "email.draft_list", params: ["session_id": item.approval.fromSession]) as? [String: Any]
        guard let raw = (result?["drafts"] as? [[String: Any]])?.first(where: { $0["id"] as? String == email.draftID }),
              var draft = SessionEmailDraft(raw) else { throw DaemonClientError.daemonError("This draft is no longer available. Prepare a new reply from its source message.") }
        if ["failed_before_submission", "confirmed_not_sent"].contains(item.state) {
            draft = try await updateEmailDraft(sessionID: item.approval.fromSession, draft: draft, to: draft.to, subject: draft.subject, body: draft.body)
        }
        let id = try await requestEmailDraftReview(sessionID: item.approval.fromSession, draft: draft)
        presentApproval(id: id)
    }
}
