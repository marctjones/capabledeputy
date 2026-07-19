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

    func testDisplayTextPreservesGeneratedImageMarkdown() {
        // #440: the daemon injects the real image markdown into the final turn
        // when the model's text omits it. displayText is the last transform
        // before the image renderer — it must not strip or mangle the markdown,
        // whether the image stands alone or trails prose.
        let path = "~/.capdep/work/images/4a5c08248e304442a14dcc7ff61c31cb.png"
        let standalone = "![a red apple on a table](\(path))"
        XCTAssertEqual(ChatContentFormatter.displayText(standalone), standalone)

        let withProse = "Here is your image:\n\n\(standalone)"
        XCTAssertTrue(ChatContentFormatter.displayText(withProse).contains(standalone))
    }

    func testAttributedMarkdownPreservesLink() {
        let rendered = ChatContentFormatter.attributedMarkdown(
            from: "Read more at [Wikipedia](https://en.wikipedia.org/wiki/Cat).",
        )
        XCTAssertFalse(String(rendered.characters).isEmpty)
    }

    func testSanitizeCommonMarkRemovesHTMLControlsAndBadLinks() {
        let raw = """
        <script>alert("x")</script>
        Click [bad](javascript:alert(1)).
        \u{001B}[31mred\u{001B}[0m
        """

        let rendered = ChatContentFormatter.displayText(raw)

        XCTAssertFalse(rendered.contains("<script>"))
        XCTAssertFalse(rendered.contains("javascript:alert"))
        XCTAssertFalse(rendered.contains("\u{001B}"))
        XCTAssertTrue(rendered.contains("unsafe-link"))
        XCTAssertTrue(rendered.contains("red"))
    }

    func testSanitizeCommonMarkPreservesHTMLInsideCodeFence() {
        let raw = """
        ```html
        <strong>kept</strong>
        ```
        """

        let rendered = ChatContentFormatter.displayText(raw)

        XCTAssertTrue(rendered.contains("<strong>kept</strong>"))
    }
}
