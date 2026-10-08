import Foundation
import XCTest
@testable import CapDepMac

final class BuildIdentityTests: XCTestCase {
    private let app = AppBuildIdentity(
        version: "0.58.0",
        executableSHA256: String(repeating: "a", count: 64),
        sourceContentSHA256: "app-current",
    )
    private let daemon = DaemonBuildIdentity(dictionary: [
        "version": "0.58.0",
        "git_rev": "0123456789abcdef0123456789abcdef01234567",
        "git_dirty": "dirty",
        "manifest_hash": "manifest",
        "source_content_hash": "content-current",
    ])

    func testCurrentCheckoutIsVerifiedBySourceContentHash() {
        XCTAssertEqual(
            BuildIdentityComparison.evaluate(
                app: app,
                daemon: daemon,
                expectedAppSourceHash: "app-current",
                expectedDaemonSourceHash: "content-current",
                connected: true,
            ),
            .current,
        )
    }

    func testStaleDaemonSourceIsKnownMismatchWithSafeGuidance() {
        let comparison = BuildIdentityComparison.evaluate(
            app: app,
            daemon: daemon,
            expectedAppSourceHash: "app-current",
            expectedDaemonSourceHash: "content-newer",
            connected: true,
        )
        XCTAssertEqual(comparison.title, "Build mismatch")
        XCTAssertNotNil(comparison.guidance)
        XCTAssertTrue(comparison.guidance?.contains("pending edits and reviews") == true)
        XCTAssertTrue(comparison.guidance?.contains("will not restart") == true)
    }

    func testPackageBuildWithoutCheckoutRemainsUsefulButUnknown() {
        let comparison = BuildIdentityComparison.evaluate(
            app: app,
            daemon: daemon,
            expectedAppSourceHash: nil,
            expectedDaemonSourceHash: nil,
            connected: true,
        )
        XCTAssertEqual(comparison.title, "Comparison unavailable")
        XCTAssertTrue(comparison.detail.contains("No app source checkout"))
        XCTAssertTrue(comparison.detail.contains("No daemon source checkout"))
    }

    func testDisconnectedDaemonIdentityIsUnknown() {
        let comparison = BuildIdentityComparison.evaluate(
            app: app,
            daemon: daemon,
            expectedAppSourceHash: "app-current",
            expectedDaemonSourceHash: "content-current",
            connected: false,
        )
        XCTAssertEqual(comparison.title, "Comparison unavailable")
        XCTAssertTrue(comparison.detail.contains("disconnected"))
    }

    func testDifferentComponentVersionsAreKnownMismatchEvenWhenHashesCouldMatch() {
        let otherApp = AppBuildIdentity(
            version: "0.57.0",
            executableSHA256: "binary",
            sourceContentSHA256: "app-current",
        )
        XCTAssertEqual(
            BuildIdentityComparison.evaluate(
                app: otherApp,
                daemon: daemon,
                expectedAppSourceHash: "app-current",
                expectedDaemonSourceHash: "content-current",
                connected: true,
            ),
            .mismatch("The app bundle version (0.57.0) differs from the daemon package version (0.58.0)."),
        )
    }

    func testDaemonIdentityParsesExistingCodeVersionContract() {
        XCTAssertEqual(daemon.version, "0.58.0")
        XCTAssertEqual(daemon.revisionLabel, "0123456789ab · modified")
        XCTAssertEqual(daemon.manifestHash, "manifest")
        XCTAssertEqual(daemon.sourceContentHash, "content-current")
    }

    func testCheckoutHashUsesDaemonContentHashFraming() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("capdep-build-identity-\(UUID().uuidString)", isDirectory: true)
        let source = root.appendingPathComponent("src/capabledeputy", isDirectory: true)
        try FileManager.default.createDirectory(at: source, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        try Data("abc".utf8).write(to: source.appendingPathComponent("fixture.py"))

        XCTAssertEqual(
            BuildIdentityComparison.daemonSourceContentHash(repositoryRoot: root),
            "1df075f268a9003fd5fc97fbb02872464d2fc4f7d3004eaaf78a84f133c9f9a4",
        )
    }

    func testAppCheckoutHashMatchesGeneratorPathFraming() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("capdep-app-build-identity-\(UUID().uuidString)", isDirectory: true)
        let source = root.appendingPathComponent("Sources", isDirectory: true)
        try FileManager.default.createDirectory(at: source, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        try Data("abc".utf8).write(to: source.appendingPathComponent("fixture.swift"))

        XCTAssertEqual(
            BuildIdentityComparison.appSourceContentHash(packageRoot: root),
            "765b46b74539066d4c8a0b668572dbbe65ba93261efa1990e871f85ea058406f",
        )
    }

    func testChangedAppSourceIsKnownMismatch() {
        XCTAssertEqual(
            BuildIdentityComparison.evaluate(
                app: app,
                daemon: daemon,
                expectedAppSourceHash: "app-newer",
                expectedDaemonSourceHash: "content-current",
                connected: true,
            ),
            .mismatch("The running app's Swift source content differs from the available checkout."),
        )
    }
}
