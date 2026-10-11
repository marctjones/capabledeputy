import XCTest
@testable import CapDepMac

final class NonEmailApprovalReviewTests: XCTestCase {
    private func approval(_ extra: [String: Any] = [:]) -> Approval {
        var raw: [String: Any] = [
            "id": 42, "action": "EXECUTE_DESTRUCTIVE", "status": "pending",
            "target": "file:///Users/someone/Documents/old-taxes.pdf",
            "justification": "Free up disk space",
            "labels_in": ["financial"], "expires_at": "2026-10-11T18:00:00Z",
        ]
        for (key, value) in extra { raw[key] = value }
        return Approval(dictionary: raw)
    }

    func testShowsTheExactFactsTheDaemonHolds() {
        let review = NonEmailApprovalReview(approval: approval())
        let byLabel = Dictionary(uniqueKeysWithValues: review.facts.map { ($0.label, $0.value) })
        XCTAssertEqual(byLabel["What"], "Delete or change data")
        XCTAssertEqual(byLabel["Target"], "file:///Users/someone/Documents/old-taxes.pdf")
        XCTAssertEqual(byLabel["Why it was requested"], "Free up disk space")
        XCTAssertEqual(byLabel["Information involved"], "financial")
        XCTAssertEqual(byLabel["Review expires"], "2026-10-11T18:00:00Z")
    }

    func testEmptyFieldsAreOmittedNotShownAsBlankRows() {
        let review = NonEmailApprovalReview(approval: approval([
            "target": "  ", "justification": "", "labels_in": [], "expires_at": "",
        ]))
        XCTAssertEqual(review.facts.map(\.label), ["What"])
    }

    func testNamesTheRealTerminalRouteForThisExactReview() {
        let review = NonEmailApprovalReview(approval: approval(["id": 7]))
        XCTAssertEqual(review.routeCommands, [
            "capdep approval show 7",
            "capdep approval approve 7",
            "capdep approval deny 7",
        ])
    }

    func testExplanationSaysNothingHappensUntilDecidedAndDoesNotOfferApproval() {
        let text = NonEmailApprovalReview(approval: approval()).explanation
        XCTAssertTrue(text.contains("nothing will happen"))
        XCTAssertTrue(text.contains("Don't proceed"))
        XCTAssertFalse(text.lowercased().contains("approve here"))
    }

    func testPlainLanguageForEveryKnownKindAndAFallbackForUnknownOnes() {
        XCTAssertEqual(NonEmailApprovalReview.plainAction("QUEUE_PURCHASE"), "Spend money")
        XCTAssertEqual(NonEmailApprovalReview.plainAction("GRANT"), "Grant access")
        XCTAssertEqual(NonEmailApprovalReview.plainAction("DECLASSIFY"), "Release information")
        XCTAssertEqual(NonEmailApprovalReview.plainAction("SOME_NEW_KIND"), "Some New Kind")
        XCTAssertEqual(NonEmailApprovalReview.plainAction(""), "Take an action")
    }
}
