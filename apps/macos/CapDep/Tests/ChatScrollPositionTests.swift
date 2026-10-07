import XCTest
@testable import CapDepMac

final class ChatScrollPositionTests: XCTestCase {
    func testGrowthDoesNotChangeReadersFollowIntent() {
        let appended = ChatScrollPosition(top: -100, bottom: 1200)
        XCTAssertTrue(appended.shouldFollow(previousTop: -100, viewportHeight: 600, wasFollowing: true))
        XCTAssertFalse(appended.shouldFollow(previousTop: -100, viewportHeight: 600, wasFollowing: false))
    }

    func testScrollingEarlierPausesAndReturningToBottomResumes() {
        XCTAssertFalse(ChatScrollPosition(top: -50, bottom: 900).shouldFollow(previousTop: -350, viewportHeight: 600, wasFollowing: true))
        XCTAssertTrue(ChatScrollPosition(top: -350, bottom: 600).shouldFollow(previousTop: -50, viewportHeight: 600, wasFollowing: false))
    }
}
