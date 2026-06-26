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
}