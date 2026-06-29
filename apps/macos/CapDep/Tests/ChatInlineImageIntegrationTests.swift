import AppKit
import XCTest
@testable import CapDepMac

final class ChatInlineImageIntegrationTests: XCTestCase {
    private var demoPath: String {
        "/Users/marc/Documents/GitHub/capabledeputy/apps/macos/CapDep/.build/demo-cat.jpg"
    }

    func testDemoCatFileExistsAndLoads() throws {
        let path = demoPath
        guard FileManager.default.fileExists(atPath: path) else {
            throw XCTSkip("demo-cat.jpg not present — run run-local-app.sh first")
        }
        let url = URL(fileURLWithPath: path)
        let values = try url.resourceValues(forKeys: [.fileSizeKey, .isReadableKey])
        XCTAssertGreaterThan(values.fileSize ?? 0, 1000)
        XCTAssertNotEqual(values.isReadable, false)
        XCTAssertNotNil(NSImage(contentsOf: url))
    }

    func testDemoMarkdownParsesAndResolvesToLoadableImage() throws {
        let path = demoPath
        guard FileManager.default.fileExists(atPath: path) else {
            throw XCTSkip("demo-cat.jpg not present")
        }
        let markdown = """
        Here is the demo cat inline:

        ![Cartoon cat](\(path))
        """
        let blocks = ChatMarkdownParser.blocks(from: markdown)
        XCTAssertTrue(blocks.contains { block in
            if case .image(let alt, let url) = block {
                return alt == "Cartoon cat" && url == path
            }
            return false
        })
        guard case .success(let resolved) = ChatImageURLResolver.resolve(path) else {
            return XCTFail("expected resolver success for demo path")
        }
        XCTAssertFalse(resolved.isRemote)
        XCTAssertNotNil(NSImage(contentsOf: resolved.url))
    }

    func testLocalDemoAttachmentSnippetUsesRealPath() throws {
        let path = demoPath
        guard FileManager.default.fileExists(atPath: path) else {
            throw XCTSkip("demo-cat.jpg not present")
        }
        let snippet = ChatImageAttachment.markdownSnippet(alt: "Cartoon cat", path: path)
        XCTAssertEqual(snippet, "![Cartoon cat](\(path))")
        let merged = ChatImageAttachment.appendSnippet(snippet!, to: "")
        XCTAssertTrue(merged?.contains(path) == true)
    }
}