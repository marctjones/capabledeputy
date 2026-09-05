import XCTest
@testable import CapDepMac

@MainActor
final class DaemonSupervisorTests: XCTestCase {
    func testHostOwnsLifecycleByDefault() {
        XCTAssertFalse(DaemonSupervisor.lifecycleOwnership(nil))
        for value in ["", "0", "false", "off", "unexpected"] {
            XCTAssertFalse(DaemonSupervisor.lifecycleOwnership(value))
        }
    }

    func testGuiOwnershipRequiresExplicitOptIn() {
        for value in ["1", "true", "yes", "on", " TRUE "] {
            XCTAssertTrue(DaemonSupervisor.lifecycleOwnership(value))
        }
    }
}
