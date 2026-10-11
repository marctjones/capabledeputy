import XCTest
@testable import CapDepMac

final class TaskApprovalTests: XCTestCase {
    private func envelope(_ extra: [String: Any] = [:]) throws -> String {
        var message: [String: Any] = ["account_id": "personal", "from_addr": "owner@example.test", "to": "recipient@example.test", "subject": "Exact subject", "body": "First line\nSecond line", "in_reply_to": "<original@example.test>", "references": "<original@example.test>", "message_id": "<reply@example.test>"]
        for (key, value) in extra { message[key] = value }
        let raw: [String: Any] = ["format": "capdep.email.v2", "draft_id": "draft", "draft_version": 3, "source_id": "source", "account_fingerprint": "account-bound", "envelope_digest": "digest", "message": message]
        return String(decoding: try JSONSerialization.data(withJSONObject: raw), as: UTF8.self)
    }

    func testReviewPreservesExactMultilineContentAndBoundIdentity() throws {
        let presentation = try XCTUnwrap(EmailReviewPresentation(payload: envelope()))
        XCTAssertTrue(presentation.isBound)
        XCTAssertEqual(presentation.body, "First line\nSecond line")
        XCTAssertEqual(presentation.to, "recipient@example.test")
        XCTAssertEqual(presentation.version, 3)
        XCTAssertEqual(presentation.draftID, "draft")
    }

    func testHiddenRecipientsAndUnsupportedPayloadCannotBeApproved() throws {
        let hidden = try XCTUnwrap(EmailReviewPresentation(payload: envelope(["bcc": "hidden@example.test"])))
        XCTAssertFalse(hidden.isBound)
        let empty = try XCTUnwrap(EmailReviewPresentation(payload: envelope(["body": "\n "])))
        XCTAssertFalse(empty.isBound)
        XCTAssertNil(EmailReviewPresentation(payload: "Assistant says approved"))
    }

    func testReceiptCannotImplyDeliveryOrEnableASecondSend() throws {
        let item = TaskApproval(["id": 12, "action": "SEND_EMAIL", "status": "approved", "review_state": "smtp_accepted", "payload": try envelope(), "can_approve": true])
        XCTAssertFalse(item.canApprove)
        XCTAssertEqual(item.title, "Accepted by your mail service")
        XCTAssertTrue(item.explanation.contains("delivery isn't confirmed"))
        XCTAssertFalse(item.permitsFreshReview)
        let unknown = TaskApproval(["review_state": "outcome_unknown", "can_approve": true])
        XCTAssertFalse(unknown.canApprove)
        XCTAssertFalse(unknown.permitsFreshReview)
        XCTAssertEqual(unknown.title, "Send outcome unknown")
    }

    func testOnlyFreshPendingProjectionEnablesDecision() {
        XCTAssertTrue(TaskApproval(["status": "pending", "review_state": "pending", "can_approve": true]).canApprove)
        for state in ["expired", "changed", "superseded", "denied", "submitting"] {
            XCTAssertFalse(TaskApproval(["status": "pending", "review_state": state, "can_approve": true]).canApprove)
        }
        XCTAssertFalse(TaskApproval(["status": "pending"]).canApprove)
    }
}
