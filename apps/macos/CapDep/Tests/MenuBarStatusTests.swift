import XCTest
@testable import CapDepMac

final class MenuBarStatusTests: XCTestCase {
    func testConnectedWithNothingPendingStatesOnlyTheConnection() {
        XCTAssertEqual(MenuBarStatus.title(connected: true, pendingCount: 0, disconnectedTitle: "Disconnected"), "Connected")
    }

    func testPendingCountIsPluralisedAndShown() {
        XCTAssertEqual(MenuBarStatus.title(connected: true, pendingCount: 1, disconnectedTitle: "x"), "Connected · 1 needs review")
        XCTAssertEqual(MenuBarStatus.title(connected: true, pendingCount: 3, disconnectedTitle: "x"), "Connected · 3 need review")
    }

    func testDisconnectedUsesTheConnectionPhaseTitle() {
        XCTAssertEqual(MenuBarStatus.title(connected: false, pendingCount: 5, disconnectedTitle: "Daemon offline"), "Daemon offline")
    }

    func testStatusNeverClaimsProtectionOrSafety() {
        let banned = ["protected", "safe", "secure", "private"]
        for pending in [0, 1, 2, 10] {
            for connected in [true, false] {
                let title = MenuBarStatus.title(connected: connected, pendingCount: pending, disconnectedTitle: "Disconnected").lowercased()
                for word in banned {
                    XCTAssertFalse(title.contains(word), "\(title) must not contain \(word)")
                }
            }
        }
    }
}
