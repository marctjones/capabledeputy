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

    func testNumberedSearchCatalogKeepsAuthoredNumbersAndLinks() {
        let raw = """
        Selected messages:

        4. [First message](https://example.com/first) - Needs a reply
        7. [Second message](https://example.com/second) - For information
        12. [Third message](https://example.com/third) - Follow up tomorrow
        """
        XCTAssertEqual(ChatContentFormatter.displayText(raw), raw)
        let rendered = ChatContentFormatter.attributedMarkdown(from: raw)
        XCTAssertEqual(String(rendered.characters), """
        Selected messages:

        4. First message - Needs a reply
        7. Second message - For information
        12. Third message - Follow up tomorrow
        """)
        XCTAssertEqual(
            rendered.runs.compactMap { $0.link?.absoluteString },
            ["https://example.com/first", "https://example.com/second", "https://example.com/third"],
        )
    }

    func testMailSummaryPreservesParagraphsHeadingsAndNestedLists() {
        let raw = """
        ## Inbox summary

        First paragraph.

        Second paragraph.

        3. Reply to Alice
           - Confirm the date
           - Keep the subject
        8. Read Bob's update

        > Quoted source remains a separate block.
        """
        XCTAssertEqual(String(ChatContentFormatter.attributedMarkdown(from: raw).characters), raw)
    }

    func testStreamingProsePreservesIncompleteMarkdownAndBlockBreaks() {
        let partials = [
            "## Inbox\n\n1. First\n2.",
            "Summary\n\n- **Unfinished emphasis",
            "Summary\n\n7. [Unfinished link](https://example.com",
        ]
        for raw in partials {
            XCTAssertEqual(String(ChatContentFormatter.attributedMarkdown(from: raw).characters), raw)
        }
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
        XCTAssertEqual(String(rendered.characters), "Read more at Wikipedia.")
        XCTAssertEqual(
            rendered.runs.compactMap { $0.link?.absoluteString },
            ["https://en.wikipedia.org/wiki/Cat"],
        )
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
