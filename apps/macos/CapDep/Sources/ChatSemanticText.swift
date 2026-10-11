import AppKit
import Markdown
import SwiftUI

/// Presentation-only Markdown. No image, file or network resolution occurs here.
@MainActor
enum ChatSemanticRenderer {
    static let maximumUTF8Bytes = 256_000

    static func codeBlocks(_ source: String) -> [String] {
        let cleaned = ChatContentFormatter.sanitizeCommonMark(source.contains("tool_calls") ? ChatContentFormatter.stripMLXArtifacts(source) : source)
        guard cleaned.utf8.count <= maximumUTF8Bytes else { return [] }
        var code: [String] = []
        func collect(_ node: any Markup) {
            if let block = node as? CodeBlock { code.append(block.code) }
            for child in node.children { collect(child) }
        }
        collect(Document(parsing: cleaned))
        return code
    }

    static func render(_ source: String, fontSize: CGFloat = 14) -> NSAttributedString {
        let cleaned = ChatContentFormatter.sanitizeCommonMark(source.contains("tool_calls") ? ChatContentFormatter.stripMLXArtifacts(source) : source)
        guard cleaned.utf8.count <= maximumUTF8Bytes else {
            return NSAttributedString(string: cleaned, attributes: [.font: NSFont.systemFont(ofSize: fontSize), .foregroundColor: NSColor.labelColor])
        }
        let document = Document(parsing: cleaned)
        let output = NSMutableAttributedString(string: "")
        let lines = cleaned.components(separatedBy: "\n")
        func append(_ string: String, font: NSFont? = nil, style: NSParagraphStyle? = nil) {
            var attributes: [NSAttributedString.Key: Any] = [.font: font ?? NSFont.systemFont(ofSize: fontSize), .foregroundColor: NSColor.labelColor]
            if let style { attributes[.paragraphStyle] = style }
            output.append(NSAttributedString(string: string, attributes: attributes))
        }
        func inline(_ node: any Markup) -> NSAttributedString {
            let result = NSMutableAttributedString(string: "")
            if let text = node as? Markdown.Text { return NSAttributedString(string: text.string) }
            if node is SoftBreak { return NSAttributedString(string: "\n") }
            if node is LineBreak { return NSAttributedString(string: "\n") }
            if let code = node as? InlineCode {
                return NSAttributedString(string: code.code, attributes: [.font: NSFont.monospacedSystemFont(ofSize: fontSize, weight: .regular), .backgroundColor: NSColor.quaternaryLabelColor.withAlphaComponent(0.12)])
            }
            if let image = node as? Markdown.Image {
                // Resource admission remains in the daemon-authorized image route.
                return NSAttributedString(string: image.plainText.isEmpty ? "[Image]" : "[Image: \(image.plainText)]")
            }
            for child in node.children { result.append(inline(child)) }
            let range = NSRange(location: 0, length: result.length)
            if node is Strong { result.addAttribute(.font, value: NSFont.boldSystemFont(ofSize: fontSize), range: range) }
            if node is Emphasis { result.addAttribute(.font, value: NSFontManager.shared.convert(NSFont.systemFont(ofSize: fontSize), toHaveTrait: .italicFontMask), range: range) }
            if node is Strikethrough { result.addAttribute(.strikethroughStyle, value: NSUnderlineStyle.single.rawValue, range: range) }
            if let link = node as? Markdown.Link, let destination = link.destination,
               let url = URL(string: destination), let scheme = url.scheme?.lowercased(),
               ["https", "http", "mailto"].contains(scheme) {
                result.addAttribute(.link, value: url, range: range)
            }
            return result
        }
        func block(_ node: any Markup, depth: Int = 0, prefix: String = "", quoted: Bool = false) {
            let style = NSMutableParagraphStyle()
            style.paragraphSpacing = 9
            style.lineSpacing = 3
            style.headIndent = CGFloat(depth) * 20 + (quoted ? 16 : 0)
            style.firstLineHeadIndent = style.headIndent
            if !prefix.isEmpty { style.headIndent += 24 }
            if let heading = node as? Heading {
                let start = output.length
                output.append(inline(heading))
                append("\n")
                output.addAttributes([.font: NSFont.boldSystemFont(ofSize: fontSize + CGFloat(max(1, 7 - heading.level)) * 2), .paragraphStyle: style], range: NSRange(location: start, length: output.length - start))
            } else if let paragraph = node as? Paragraph {
                let start = output.length
                append(prefix)
                output.append(inline(paragraph))
                append("\n")
                let range = NSRange(location: start, length: output.length - start)
                output.addAttribute(.paragraphStyle, value: style, range: range)
                if quoted { output.addAttribute(.foregroundColor, value: NSColor.secondaryLabelColor, range: range) }
            } else if let code = node as? CodeBlock {
                let start = output.length
                // cmark preserves code whitespace and the content's trailing newline.
                append(code.code, font: NSFont.monospacedSystemFont(ofSize: fontSize, weight: .regular), style: style)
                if !code.code.hasSuffix("\n") { append("\n") }
                output.addAttribute(.backgroundColor, value: NSColor.quaternaryLabelColor.withAlphaComponent(0.12), range: NSRange(location: start, length: output.length - start))
            } else if node is OrderedList || node is UnorderedList {
                for (index, item) in node.children.enumerated() {
                    var marker = "• "
                    if let list = node as? OrderedList {
                        marker = "\(list.startIndex + UInt(index)). "
                        if let line = item.range?.lowerBound.line, line > 0, line <= lines.count,
                           let match = lines[line - 1].firstMatch(of: /^\s*([0-9]+[.)])\s+/) {
                            marker = String(match.1) + " "
                        }
                    }
                    if let item = item as? ListItem, let checkbox = item.checkbox {
                        marker += checkbox == .checked ? "☑ " : "☐ "
                    }
                    for (childIndex, child) in item.children.enumerated() {
                        block(child, depth: child is OrderedList || child is UnorderedList ? depth + 1 : depth, prefix: childIndex == 0 ? marker : "", quoted: quoted)
                    }
                }
            } else if node is BlockQuote {
                for child in node.children { block(child, depth: depth + 1, quoted: true) }
            } else if let table = node as? Markdown.Table {
                let native = NSTextTable()
                native.numberOfColumns = table.maxColumnCount
                native.collapsesBorders = true
                native.layoutAlgorithm = .automaticLayoutAlgorithm
                let rows: [any Markup] = [table.head] + Array(table.body.children)
                for (rowIndex, row) in rows.enumerated() {
                    for (column, cell) in row.children.enumerated() {
                        let cellStyle = NSMutableParagraphStyle()
                        let cellBlock = NSTextTableBlock(table: native, startingRow: rowIndex, rowSpan: 1, startingColumn: column, columnSpan: 1)
                        cellBlock.setWidth(5, type: .absoluteValueType, for: .padding)
                        cellBlock.setWidth(0.5, type: .absoluteValueType, for: .border)
                        cellBlock.setBorderColor(.separatorColor)
                        cellStyle.textBlocks = [cellBlock]
                        if column < table.columnAlignments.count {
                            switch table.columnAlignments[column] {
                            case .center: cellStyle.alignment = .center
                            case .right: cellStyle.alignment = .right
                            default: cellStyle.alignment = .left
                            }
                        }
                        let start = output.length
                        output.append(inline(cell))
                        append("\n")
                        output.addAttribute(.paragraphStyle, value: cellStyle, range: NSRange(location: start, length: output.length - start))
                        if rowIndex == 0 { output.addAttribute(.font, value: NSFont.boldSystemFont(ofSize: fontSize), range: NSRange(location: start, length: output.length - start)) }
                    }
                }
            } else if node is ThematicBreak {
                append("────────\n", style: style)
            } else {
                for child in node.children { block(child, depth: depth, quoted: quoted) }
            }
        }
        for child in document.children { block(child) }
        // Apply defaults only where an inline/block node has not specified a style.
        let full = NSRange(location: 0, length: output.length)
        output.enumerateAttributes(in: full) { attributes, range, _ in
            if attributes[.font] == nil { output.addAttribute(.font, value: NSFont.systemFont(ofSize: fontSize), range: range) }
            if attributes[.foregroundColor] == nil { output.addAttribute(.foregroundColor, value: NSColor.labelColor, range: range) }
        }
        return output
    }
}

/// One TextKit surface provides contiguous selection and normal macOS copy.
struct ChatSemanticText: NSViewRepresentable {
    let source: String
    @AppStorage("capdep.chat.text-size") private var fontSize = 14.0

    final class Coordinator {
        var source: String?
        var fontSize: Double?
    }
    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeNSView(context: Context) -> NSTextView {
        let view = NSTextView()
        view.isEditable = false
        view.isSelectable = true
        view.drawsBackground = false
        view.textContainerInset = .zero
        view.textContainer?.lineFragmentPadding = 0
        view.textContainer?.widthTracksTextView = true
        view.isVerticallyResizable = true
        view.isHorizontallyResizable = false
        view.setAccessibilityIdentifier("capdep.chat.semantic-text")
        return view
    }

    func updateNSView(_ view: NSTextView, context: Context) {
        guard context.coordinator.source != source || context.coordinator.fontSize != fontSize else { return }
        context.coordinator.source = source
        context.coordinator.fontSize = fontSize
        let rendered = ChatSemanticRenderer.render(source, fontSize: fontSize)
        guard view.textStorage?.isEqual(to: rendered) != true else { return }
        let selected = view.selectedRanges
        view.textStorage?.setAttributedString(rendered)
        // Preserve a selection through append-only streaming updates, clipped if needed.
        view.selectedRanges = selected.map { value in
            let range = value.rangeValue
            let start = min(range.location, rendered.length)
            return NSValue(range: NSRange(location: start, length: min(range.length, rendered.length - start)))
        }
    }

    func sizeThatFits(_ proposal: ProposedViewSize, nsView: NSTextView, context: Context) -> CGSize? {
        let width = max(1, proposal.width ?? 600)
        nsView.textContainer?.containerSize = NSSize(width: width, height: .greatestFiniteMagnitude)
        guard let layout = nsView.layoutManager, let container = nsView.textContainer else { return nil }
        layout.ensureLayout(for: container)
        return CGSize(width: width, height: ceil(layout.usedRect(for: container).height) + 2)
    }
}
