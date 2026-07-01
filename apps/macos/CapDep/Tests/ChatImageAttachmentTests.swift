import XCTest
@testable import CapDepMac

final class ChatImageAttachmentTests: XCTestCase {
    func testAppendSnippetAddsMarkdownImage() {
        let merged = ChatImageAttachment.appendSnippet(
            "![plot](/tmp/plot.png)",
            to: "Here is the result:",
        )
        XCTAssertEqual(merged, "Here is the result:\n\n![plot](/tmp/plot.png)")
    }

    func testAppendSnippetSkipsDuplicatePath() {
        let existing = "Done.\n\n![plot](/tmp/plot.png)"
        let merged = ChatImageAttachment.appendSnippet("![again](/tmp/plot.png)", to: existing)
        XCTAssertNil(merged)
    }

    func testPreserveImageSnippetsKeepsStructuredAttachmentOnFinalText() {
        let streamed = "Rendering...\n\n![generated](/tmp/generated.png)"
        let merged = ChatImageAttachment.preserveImageSnippets(
            from: streamed,
            in: "Here is the image.",
        )
        XCTAssertEqual(merged, "Here is the image.\n\n![generated](/tmp/generated.png)")
    }

    func testPreserveImageSnippetsDoesNotDuplicateFinalMarkdown() {
        let streamed = "Rendering...\n\n![generated](/tmp/generated.png)"
        let final = "Done.\n\n![generated](/tmp/generated.png)"
        XCTAssertEqual(ChatImageAttachment.preserveImageSnippets(from: streamed, in: final), final)
    }
}
