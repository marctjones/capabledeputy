import AppKit
import XCTest
@testable import CapDepMac

final class ChatSemanticRendererTests: XCTestCase {
    func testSemanticFixtureKeepsAuthoredTextAndBlockAttributes() async throws {
        let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        let source = try String(contentsOf: root.appendingPathComponent("tests/fixtures/commonmark/native-semantic.md"), encoding: .utf8)
        await MainActor.run {
            let rendered = ChatSemanticRenderer.render(source)
            XCTAssertTrue(rendered.string.contains("4. First authored item\n7. Second authored item"))
            XCTAssertTrue(rendered.string.contains("☑ Completed task (inert)"))
            XCTAssertTrue(rendered.string.contains("café, 日本語, مرحبا"))
            XCTAssertTrue(rendered.string.contains("Quoted paragraph.\nAnother quoted paragraph."))
            XCTAssertTrue(rendered.string.contains("Final paragraph."))
            let heading = rendered.attribute(.font, at: 0, effectiveRange: nil) as? NSFont
            XCTAssertGreaterThan(heading?.pointSize ?? 0, 14)
            let obsolete = (rendered.string as NSString).range(of: "obsolete wording")
            XCTAssertNotNil(rendered.attribute(.strikethroughStyle, at: obsolete.location, effectiveRange: nil))
            let cell = (rendered.string as NSString).range(of: "Alice")
            let style = rendered.attribute(.paragraphStyle, at: cell.location, effectiveRange: nil) as? NSParagraphStyle
            XCTAssertTrue(style?.textBlocks.first is NSTextTableBlock)
            let link = (rendered.string as NSString).range(of: "https://example.invalid/path")
            XCTAssertNotNil(rendered.attribute(.link, at: link.location, effectiveRange: nil))
            XCTAssertEqual(ChatSemanticRenderer.codeBlocks(source), ["let answer = 42\n\n    print(answer)\n"])
        }
    }

    func testIncompleteFenceAndCodeCopyPreserveWhitespace() async {
        await MainActor.run {
            let source = "Before\n\n````python\n\n  value = 1\n\n"
            let result = ChatSemanticRenderer.render(source)
            XCTAssertTrue(result.string.contains("Before\n\n  value = 1\n\n"))
            XCTAssertEqual(ChatSemanticRenderer.codeBlocks(source), ["\n  value = 1\n\n"])
        }
    }

    func testUntrustedLinksImagesAndHTMLDoNotResolveResources() async {
        await MainActor.run {
            let result = ChatSemanticRenderer.render("[unsafe](file:///tmp/private)\n\n![private](/tmp/private.png)\n\n<script>bad()</script>")
            XCTAssertNil(result.attribute(.link, at: 0, effectiveRange: nil))
            XCTAssertTrue(result.string.contains("[Image: private]"))
            XCTAssertFalse(result.string.contains("<script>"))
        }
    }

    func testLargeInputUsesExactReadableFallback() async {
        let source = String(repeating: "# very large **source**\n", count: 15_000)
        await MainActor.run {
            XCTAssertEqual(ChatSemanticRenderer.render(source).string, source)
        }
    }
}

extension ChatSemanticRendererTests {
    func testNativeLayoutSelectionAndOptionalVisualEvidence() async throws {
        let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        let source = try String(contentsOf: root.appendingPathComponent("tests/fixtures/commonmark/native-semantic.md"), encoding: .utf8)
        try await MainActor.run {
            _ = NSApplication.shared
            for (width, size, dark) in [(380.0, 14.0, false), (900.0, 14.0, true), (380.0, 22.0, false), (900.0, 22.0, true)] {
                let text = NSTextView(frame: NSRect(x: 0, y: 0, width: width, height: 1200))
                text.isEditable = false
                text.isSelectable = true
                text.appearance = NSAppearance(named: dark ? .darkAqua : .aqua)
                text.backgroundColor = .textBackgroundColor
                text.textContainer?.containerSize = NSSize(width: width - 12, height: .greatestFiniteMagnitude)
                text.textContainer?.widthTracksTextView = false
                text.textStorage?.setAttributedString(ChatSemanticRenderer.render(source, fontSize: size))
                let layout = try XCTUnwrap(text.layoutManager)
                let container = try XCTUnwrap(text.textContainer)
                layout.ensureLayout(for: container)
                XCTAssertLessThanOrEqual(layout.usedRect(for: container).width, width)
                let string = text.string as NSString
                let start = string.range(of: "First paragraph").location
                let endRange = string.range(of: "7. Second authored item")
                let selected = NSRange(location: start, length: NSMaxRange(endRange) - start)
                text.setSelectedRange(selected)
                let pasteboard = NSPasteboard.withUniqueName()
                defer { pasteboard.releaseGlobally() }
                XCTAssertEqual(text.selectedRange(), selected)
                XCTAssertTrue(text.writeSelection(to: pasteboard, types: text.writablePasteboardTypes))
                XCTAssertEqual(pasteboard.string(forType: .string), string.substring(with: selected))
                if let evidence = ProcessInfo.processInfo.environment["CAPDEP_NATIVE_RENDER_EVIDENCE"] {
                    text.setSelectedRange(NSRange(location: 0, length: 0))
                    let bitmap = try XCTUnwrap(text.bitmapImageRepForCachingDisplay(in: text.bounds))
                    text.cacheDisplay(in: text.bounds, to: bitmap)
                    let png = try XCTUnwrap(bitmap.representation(using: .png, properties: [:]))
                    try FileManager.default.createDirectory(atPath: evidence, withIntermediateDirectories: true)
                    try png.write(to: URL(fileURLWithPath: evidence).appendingPathComponent("native-\(Int(width))-\(Int(size))-\(dark ? "dark" : "light").png"))
                }
            }
        }
    }
}
