import XCTest
@testable import CapDepMac

final class ChatMarkdownParserTests: XCTestCase {
    func testParsesFencedCodeBlock() {
        let blocks = ChatMarkdownParser.blocks(from: """
        Intro paragraph.

        ```python
        print("hi")
        ```
        """)
        XCTAssertEqual(blocks.count, 2)
        if case .prose(let text) = blocks[0] {
            XCTAssertTrue(text.contains("Intro"))
        } else {
            XCTFail("expected prose")
        }
        if case .code(let language, let body) = blocks[1] {
            XCTAssertEqual(language, "python")
            XCTAssertTrue(body.contains("print"))
        } else {
            XCTFail("expected code")
        }
    }

    func testParsesMarkdownImage() {
        let blocks = ChatMarkdownParser.blocks(from: "See this:\n\n![diagram](https://example.com/a.png)")
        XCTAssertEqual(blocks.count, 2)
        if case .image(let alt, let url) = blocks[1] {
            XCTAssertEqual(alt, "diagram")
            XCTAssertEqual(url, "https://example.com/a.png")
        } else {
            XCTFail("expected image")
        }
    }

    func testStreamingHoldsIncompleteImageMarkdown() {
        let partial = "Here is the chart:\n\n![plot](/tmp/chart.pn"
        let blocks = ChatMarkdownParser.blocks(from: partial, isStreaming: true)
        XCTAssertEqual(blocks.count, 1)
        if case .prose(let text) = blocks[0] {
            XCTAssertTrue(text.contains("![plot](/tmp/chart.pn"))
        } else {
            XCTFail("expected prose while image markdown is incomplete")
        }
    }

    func testStreamingPromotesCompleteImageMidStream() {
        let partial = "Here is the chart:\n\n![plot](/tmp/chart.png)\n\nStill writing…"
        let blocks = ChatMarkdownParser.blocks(from: partial, isStreaming: true)
        XCTAssertEqual(blocks.count, 3)
        if case .prose(let intro) = blocks[0] {
            XCTAssertTrue(intro.contains("Here is the chart"))
        } else {
            XCTFail("expected intro prose")
        }
        if case .image(let alt, let url) = blocks[1] {
            XCTAssertEqual(alt, "plot")
            XCTAssertEqual(url, "/tmp/chart.png")
        } else {
            XCTFail("expected image once markdown is complete")
        }
        if case .prose(let tail) = blocks[2] {
            XCTAssertTrue(tail.contains("Still writing"))
        } else {
            XCTFail("expected trailing prose")
        }
    }

    func testTrailingIncompleteImageSuffixDetection() {
        XCTAssertEqual(
            ChatMarkdownParser.trailingIncompleteImageSuffix(in: "![alt](/path"),
            "![alt](/path",
        )
        XCTAssertNil(
            ChatMarkdownParser.trailingIncompleteImageSuffix(in: "![alt](/path.png)"),
        )
    }

    func testHoldsUnverifiedGeneratedWorkImageDuringStreaming() {
        let dog = "~/.capdep/work/images/dog.png"
        let markdown = "Here:\n\n![dog](\(dog))"
        let blocks = ChatMarkdownParser.blocks(
            from: markdown,
            isStreaming: true,
            authorizedImagePaths: [],
            holdUnverifiedGeneratedImages: true,
        )
        XCTAssertEqual(blocks.count, 2)
        if case .prose(let placeholder) = blocks[1] {
            XCTAssertTrue(placeholder.contains("Generating image"))
        } else {
            XCTFail("expected generating placeholder")
        }
    }

    func testShowsAuthorizedGeneratedWorkImageDuringStreaming() {
        let woman = "~/.capdep/work/images/woman.png"
        let expanded = NSString(string: woman).expandingTildeInPath
        let markdown = "![woman](\(woman))"
        let blocks = ChatMarkdownParser.blocks(
            from: markdown,
            isStreaming: true,
            authorizedImagePaths: [expanded],
            holdUnverifiedGeneratedImages: true,
        )
        XCTAssertEqual(blocks.count, 1)
        if case .image(let alt, let url) = blocks[0] {
            XCTAssertEqual(alt, "woman")
            XCTAssertEqual(url, woman)
        } else {
            XCTFail("expected image block")
        }
    }

    func testStripUnverifiedGeneratedImageMarkdown() {
        let dog = "~/.capdep/work/images/dog.png"
        let woman = "~/.capdep/work/images/woman.png"
        let expandedWoman = NSString(string: woman).expandingTildeInPath
        let markdown = "See:\n\n![dog](\(dog))\n\n![woman](\(woman))"
        let cleaned = ChatMarkdownParser.stripUnverifiedGeneratedImageMarkdown(
            from: markdown,
            authorizedImagePaths: [expandedWoman],
        )
        XCTAssertFalse(cleaned.contains(dog))
        XCTAssertTrue(cleaned.contains(woman))
    }
}