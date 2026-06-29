import XCTest
@testable import CapDepMac

final class ChatImageURLResolverTests: XCTestCase {
    func testResolvesAbsolutePNGPath() throws {
        let temp = FileManager.default.temporaryDirectory
            .appendingPathComponent("capdep-image-test-\(UUID().uuidString).png")
        defer { try? FileManager.default.removeItem(at: temp) }
        try Data([0x89, 0x50, 0x4E, 0x47]).write(to: temp)

        let result = ChatImageURLResolver.resolve(temp.path)
        guard case .success(let resolved) = result else {
            return XCTFail("expected success, got \(result)")
        }
        XCTAssertFalse(resolved.isRemote)
        XCTAssertFalse(resolved.isAnimatedGIF)
        XCTAssertEqual(resolved.url.path, temp.path)
    }

    func testRejectsOversizedFile() throws {
        let temp = FileManager.default.temporaryDirectory
            .appendingPathComponent("capdep-image-test-\(UUID().uuidString).jpg")
        defer { try? FileManager.default.removeItem(at: temp) }
        try Data(count: Int(ChatImageURLResolver.maxBytes) + 1).write(to: temp)

        let result = ChatImageURLResolver.resolve(temp.path)
        guard case .failure(.tooLarge) = result else {
            return XCTFail("expected tooLarge, got \(result)")
        }
    }

    func testResolvesHTTPSURL() {
        let result = ChatImageURLResolver.resolve("https://example.com/chart.png")
        guard case .success(let resolved) = result else {
            return XCTFail("expected success, got \(result)")
        }
        XCTAssertTrue(resolved.isRemote)
    }

    func testMatchesGrantPatternForDirectoryWildcard() {
        XCTAssertTrue(
            ChatImageURLResolver.matchesGrantPattern(
                path: "/tmp/project/output/plot.png",
                pattern: "/tmp/project/*",
            ),
        )
        XCTAssertFalse(
            ChatImageURLResolver.matchesGrantPattern(
                path: "/tmp/other/plot.png",
                pattern: "/tmp/project/*",
            ),
        )
    }
}