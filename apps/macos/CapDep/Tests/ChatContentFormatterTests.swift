import XCTest
@testable import CapDepMac

final class ChatContentFormatterTests: XCTestCase {
    func testStripMLXToolCallPrefix() {
        let raw = #"{"tool_calls": [{"id": "1", "name": "kagi.kagi_search_fetch"}]}The answer is cats."#
        XCTAssertEqual(
            ChatContentFormatter.displayText(raw),
            "The answer is cats.",
        )
    }

    func testCondenseNumberedSearchCatalog() {
        let raw = """
        Here is what I found about cats.

        1. [Cat - Wikipedia](https://en.wikipedia.org/wiki/Cat) - Domestic species
        2. [Felidae - Wikipedia](https://en.wikipedia.org/wiki/Felidae) - Mammal family
        3. [Cats musical - Wikipedia](https://en.wikipedia.org/wiki/Cats_(musical)) - Stage show
        """
        let formatted = ChatContentFormatter.displayText(raw)
        XCTAssertTrue(formatted.contains("- **Cat - Wikipedia**"))
        XCTAssertTrue(formatted.contains("[source](https://en.wikipedia.org/wiki/Cat)"))
        XCTAssertFalse(formatted.contains("1. [Cat"))
    }

    func testAttributedMarkdownPreservesLink() {
        let rendered = ChatContentFormatter.attributedMarkdown(
            from: "Read more at [Wikipedia](https://en.wikipedia.org/wiki/Cat).",
        )
        XCTAssertFalse(String(rendered.characters).isEmpty)
    }
}