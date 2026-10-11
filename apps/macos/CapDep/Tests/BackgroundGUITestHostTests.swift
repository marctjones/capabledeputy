import AppKit
import XCTest
@testable import CapDepMac

#if DEBUG
final class BackgroundGUITestHostTests: XCTestCase {
    @MainActor
    func testEntrypointRequiresExplicitOptIn() throws {
        guard ProcessInfo.processInfo.environment["CAPDEP_GUI_BACKGROUND_TEST"] != "1" else {
            throw XCTSkip("The isolated GUI launcher explicitly opted in")
        }
        XCTAssertFalse(CapDepBackgroundGUIEntrypoint.runIfEnabled())
    }

    @MainActor
    func testBackgroundPanelCannotTakeKeyboardOrMainWindowFocus() {
        _ = NSApplication.shared
        let panel = CapDepBackgroundPanel(title: "Background test", size: NSSize(width: 720, height: 600))
        XCTAssertFalse(panel.canBecomeKey)
        XCTAssertFalse(panel.canBecomeMain)
        XCTAssertTrue(panel.styleMask.contains(.nonactivatingPanel))
        XCTAssertFalse(panel.isFloatingPanel)
        XCTAssertFalse(panel.hidesOnDeactivate)
        XCTAssertEqual(panel.level, .normal)
        XCTAssertFalse(panel.isVisible)
        panel.close()
    }
}
#endif
