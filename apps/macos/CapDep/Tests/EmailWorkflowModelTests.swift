import XCTest
@testable import CapDepMac

final class EmailWorkflowModelTests: XCTestCase {
    func testExactReviewPreservesAccountThreadAndMultilineContent() throws {
        let body = "First paragraph.\n\n3. Keep this number.\n8. Keep this one too."
        let payload: [String: Any] = [
            "format": "capdep.email.v2", "source_id": "source-1", "draft_version": 4,
            "message": ["account_id": "account-1", "from_addr": "me@example.test",
                        "to": "reply@example.test", "subject": "Re: Meeting", "body": body,
                        "in_reply_to": "<source@example.test>", "message_id": "<outbound@example.test>",
                        "references": "<earlier@example.test> <source@example.test>"],
        ]
        let json = String(decoding: try JSONSerialization.data(withJSONObject: payload), as: UTF8.self)
        let review = try XCTUnwrap(EmailReviewPresentation(payload: json))
        XCTAssertEqual(review.body, body)
        XCTAssertEqual(review.version, 4)
        XCTAssertEqual(review.account, "account-1")
        XCTAssertEqual(review.inReplyTo, "<source@example.test>")
        XCTAssertTrue(review.exactText.contains(body))
        XCTAssertNil(EmailReviewPresentation(payload: "Approve this reply"))
        XCTAssertNil(EmailReviewPresentation(payload: "{\"format\":\"untrusted.v1\",\"message\":{}}"))
    }

    func testUnknownOutcomeIsNotPresentedAsUnsentOrDelivered() throws {
        let operation = try XCTUnwrap(EmailOperation([
            "id": "operation-1", "status": "outcome_unknown", "envelope": ["subject": "Reply", "to": "test@example.test"],
            "reconciliation_outcome": "confirmed_not_sent", "reconciliation_reason": "Provider verified rejection",
        ]))
        XCTAssertTrue(operation.statusText.contains("Outcome unknown"))
        XCTAssertEqual(operation.reconciliation, "confirmed_not_sent")
        XCTAssertEqual(operation.subject, "Reply")
        let accepted = try XCTUnwrap(EmailOperation(["id": "accepted", "status": "smtp_accepted"]))
        XCTAssertTrue(accepted.statusText.contains("recipient delivery unverified"))
    }

    func testSourceCoverageFailsClosedWhenMetadataMissing() throws {
        let source = try XCTUnwrap(SessionEmailSource(["id": "source-1"]))
        XCTAssertTrue(source.truncated)
        XCTAssertFalse(source.bodyPresent)
        let draft = try XCTUnwrap(SessionEmailDraft(["id": "draft-1", "version": 2, "source_id": "source-1", "account_id": "account-1", "from_addr": "me@example.test"]))
        XCTAssertEqual(draft.sourceID, source.id)
        XCTAssertEqual(draft.version, 2)
        XCTAssertEqual(draft.fromAddress, "me@example.test")
    }

    func testReceiptKeepsFrozenIdentityForProviderVerification() throws {
        let receipt = try XCTUnwrap(EmailOperation([
            "id": "operation-9", "draft_version": 3, "status": "outcome_unknown",
            "subject": "Exact reviewed subject", "to": "alice@example.test",
            "from_addr": "owner@example.test", "account_id": "personal-imap",
            "message_id": "<draft.v3@capabledeputy.local>",
        ]))
        XCTAssertTrue(receipt.identityText.contains("To: alice@example.test"))
        XCTAssertTrue(receipt.identityText.contains("Account: personal-imap"))
        XCTAssertTrue(receipt.identityText.contains("Message-ID: <draft.v3@capabledeputy.local>"))
        XCTAssertTrue(receipt.identityText.contains("Draft version: 3"))
        XCTAssertTrue(receipt.identityText.contains("Operation: operation-9"))
    }
}
