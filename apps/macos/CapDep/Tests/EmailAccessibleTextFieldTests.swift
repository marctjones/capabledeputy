import AppKit
import XCTest
@testable import CapDepMac

final class EmailAccessibleTextFieldTests: XCTestCase {
    @MainActor
    func testAccessibilityEditsReachBindingWithoutFirstResponder() {
        _ = NSApplication.shared
        let field = EmailAccessibleTextField()
        field.isEditable = true
        var boundText = "Original subject"
        field.valueChanged = { boundText = $0 }
        field.setAccessibilityValue("Edited subject — exact review")
        XCTAssertNil(field.window)
        XCTAssertEqual(field.stringValue, "Edited subject — exact review")
        XCTAssertEqual(boundText, field.stringValue)

        field.isEnabled = false
        field.setAccessibilityValue("Must not overwrite a pending save")
        XCTAssertEqual(boundText, "Edited subject — exact review")
        XCTAssertEqual(field.stringValue, boundText)
    }
}
