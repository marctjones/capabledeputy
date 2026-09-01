import AppKit
import Foundation
import XCTest

@testable import CapDepMac

/// Pins the AppleScript terminology contract: Cocoa scripting resolves the
/// `cocoa class` / `cocoa key` names in CapDep.sdef through the ObjC runtime
/// at event time, with no build-time check — if a name drifts, scripting
/// breaks silently. These tests fail loudly instead.
final class AppleScriptSupportTests: XCTestCase {
    private static let sdefURL = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent()
        .deletingLastPathComponent()
        .appendingPathComponent("CapDep.sdef")

    private func sdefDocument() throws -> XMLDocument {
        try XMLDocument(contentsOf: Self.sdefURL, options: [])
    }

    func testSdefIsValidXML() throws {
        XCTAssertNoThrow(try sdefDocument())
    }

    func testEveryCommandClassInSdefResolvesToAnObjCClass() throws {
        let document = try sdefDocument()
        let classNames = try XCTUnwrap(
            document.rootElement()?
                .nodes(forXPath: "//command/cocoa/@class")
                .compactMap(\.stringValue)
        )
        XCTAssertFalse(classNames.isEmpty, "CapDep.sdef declares no command classes")
        for name in classNames {
            let resolved = NSClassFromString(name)
            XCTAssertNotNil(resolved, "sdef cocoa class \(name) is not registered with the ObjC runtime")
            XCTAssertTrue(
                resolved is NSScriptCommand.Type,
                "sdef cocoa class \(name) must subclass NSScriptCommand",
            )
        }
    }

    func testEveryApplicationPropertyKeyIsHandledByTheScriptingDelegate() throws {
        let document = try sdefDocument()
        let cocoaKeys = try XCTUnwrap(
            document.rootElement()?
                .nodes(forXPath: "//class[@name='application']/property/cocoa/@key")
                .compactMap(\.stringValue)
        )
        XCTAssertFalse(cocoaKeys.isEmpty, "CapDep.sdef declares no application properties")
        XCTAssertEqual(
            Set(cocoaKeys),
            CapDepScriptingDelegate.scriptingKeys,
            "sdef application-property keys and CapDepScriptingDelegate.scriptingKeys must stay in lockstep",
        )
    }

    @MainActor
    func testScriptingDelegateClaimsExactlyItsScriptingKeys() {
        let delegate = CapDepScriptingDelegate()
        for key in CapDepScriptingDelegate.scriptingKeys {
            XCTAssertTrue(delegate.application(NSApplication.shared, delegateHandlesKey: key))
        }
        XCTAssertFalse(delegate.application(NSApplication.shared, delegateHandlesKey: "unrelatedKey"))
    }

    @MainActor
    func testScriptingPropertiesFailSafeWithoutARunningModel() {
        // KVC reads must not crash (or claim a daemon) before the app model
        // exists — osascript can race app launch.
        if CapDepAppModel.shared == nil {
            let delegate = CapDepScriptingDelegate()
            XCTAssertFalse(delegate.daemonConnected)
            XCTAssertEqual(delegate.currentSessionID, "")
            XCTAssertEqual(delegate.pendingApprovalIDs, [])
        }
    }
}
