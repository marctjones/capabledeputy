import XCTest
@testable import CapDepMac

final class ApprovalNotificationRoutingTests: XCTestCase {
    private func approval(_ id: Int) -> Approval {
        Approval(dictionary: ["id": id, "action": "SEND_EMAIL", "status": "pending"])
    }

    func testNewestUnannouncedApprovalIsChosenNotTheOldestPending() {
        // #10 is old and already announced; #11 just arrived.
        let chosen = ApprovalNotificationRouting.approvalToAnnounce(
            announced: [10],
            pending: [approval(10), approval(11)],
        )
        XCTAssertEqual(chosen, 11)
    }

    func testNothingNewMeansNoNotification() {
        XCTAssertNil(
            ApprovalNotificationRouting.approvalToAnnounce(announced: [10, 11], pending: [approval(10), approval(11)]),
        )
        XCTAssertNil(ApprovalNotificationRouting.approvalToAnnounce(announced: [], pending: []))
    }

    func testSeveralArrivingAtOnceAnnouncesTheNewest() {
        let chosen = ApprovalNotificationRouting.approvalToAnnounce(
            announced: [],
            pending: [approval(3), approval(9), approval(5)],
        )
        XCTAssertEqual(chosen, 9)
    }

    func testResolvedApprovalsDoNotRetriggerWhenCountDropsThenRises() {
        // Count-based logic re-notified after 11 was resolved and 12 arrived with
        // a stale first(). Id-based logic announces exactly 12.
        let chosen = ApprovalNotificationRouting.approvalToAnnounce(
            announced: [10, 11],
            pending: [approval(10), approval(12)],
        )
        XCTAssertEqual(chosen, 12)
    }
}
