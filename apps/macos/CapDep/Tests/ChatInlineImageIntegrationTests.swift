import AppKit
import XCTest
@testable import CapDepMac

final class ChatInlineImageIntegrationTests: XCTestCase {
    private var demoPath: String? {
        let appSupport = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/CapDep/media/demo-cat.jpg")
            .path
        if FileManager.default.fileExists(atPath: appSupport) {
            return appSupport
        }
        let buildPath = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent(".build/demo-cat.jpg")
            .path
        return FileManager.default.fileExists(atPath: buildPath) ? buildPath : nil
    }

    func testDemoCatFileExistsAndLoads() throws {
        guard let path = demoPath else {
            throw XCTSkip("demo-cat.jpg not present — run run-local-app.sh first")
        }
        let url = URL(fileURLWithPath: path)
        let values = try url.resourceValues(forKeys: [.fileSizeKey, .isReadableKey])
        XCTAssertGreaterThan(values.fileSize ?? 0, 1000)
        XCTAssertNotEqual(values.isReadable, false)
        XCTAssertNotNil(NSImage(contentsOf: url))
    }

    func testDemoMarkdownParsesAndResolvesToLoadableImage() throws {
        guard let path = demoPath else {
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
        guard let path = demoPath else {
            throw XCTSkip("demo-cat.jpg not present")
        }
        let snippet = ChatImageAttachment.markdownSnippet(alt: "Cartoon cat", path: path)
        XCTAssertEqual(snippet, "![Cartoon cat](\(path))")
        let merged = ChatImageAttachment.appendSnippet(snippet!, to: "")
        XCTAssertTrue(merged?.contains(path) == true)
    }

    func testGeneratedWorkImagesParseResolveAndLoad() throws {
        let workDir = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".capdep/work/images")
        let candidates = [
            ("dog", workDir.appendingPathComponent("dog.png")),
            ("woman", workDir.appendingPathComponent("dbbaf0ef2fda4a0eb822bb3803eff556.png")),
        ]
        var checked = 0
        for (label, url) in candidates {
            guard FileManager.default.fileExists(atPath: url.path) else { continue }
            checked += 1
            let path = url.path
            let markdown = "![\(label)](\(path))"
            let blocks = ChatMarkdownParser.blocks(from: markdown)
            XCTAssertTrue(blocks.contains { block in
                if case .image(let alt, let imagePath) = block {
                    return alt == label && imagePath == path
                }
                return false
            })
            guard case .success(let resolved) = ChatImageURLResolver.resolve(path) else {
                return XCTFail("expected resolver success for generated \(label) path")
            }
            XCTAssertFalse(resolved.isRemote)
            XCTAssertNotNil(NSImage(contentsOf: resolved.url))
        }
        if checked == 0 {
            throw XCTSkip("no generated work/images fixtures on disk")
        }
    }
}