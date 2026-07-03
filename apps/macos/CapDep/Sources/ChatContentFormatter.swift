import Foundation

/// Normalizes assistant text for the chat surface: strips MLX artifacts and
/// renders lightweight markdown (links, emphasis, lists) as AttributedString.
enum ChatContentFormatter {
    static func displayText(_ raw: String) -> String {
        var text = stripMLXArtifacts(raw)
        text = sanitizeCommonMark(text)
        text = condenseVerboseSearchCatalog(text)
        return text.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    static func attributedMarkdown(from raw: String, fullDocument: Bool = false) -> AttributedString {
        let cleaned = displayText(raw)
        guard !cleaned.isEmpty else {
            return AttributedString("")
        }
        var options = AttributedString.MarkdownParsingOptions()
        options.interpretedSyntax = fullDocument ? .full : .inlineOnlyPreservingWhitespace
        if let parsed = try? AttributedString(markdown: cleaned, options: options) {
            return parsed
        }
        return AttributedString(cleaned)
    }

    /// MLX sometimes prefixes user-facing prose with a partial `{"tool_calls":…}` blob.
    static func stripMLXArtifacts(_ text: String) -> String {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed.contains("tool_calls") else {
            return trimmed
        }
        if let endMarker = trimmed.range(of: "}]}") {
            let tail = String(trimmed[endMarker.upperBound...])
                .trimmingCharacters(in: .whitespacesAndNewlines)
            if !tail.isEmpty {
                return tail
            }
        }
        if let endMarker = trimmed.range(of: "}]") {
            let tail = String(trimmed[endMarker.upperBound...])
                .trimmingCharacters(in: .whitespacesAndNewlines)
            if !tail.isEmpty, !tail.hasPrefix("{") {
                return tail
            }
        }
        return trimmed
    }

    static func sanitizeCommonMark(_ text: String) -> String {
        var sanitized = text
        sanitized = sanitized.replacing(/\u{001B}\[[0-?]*[ -\/]*[@-~]/, with: "")
        sanitized = sanitized.replacing(/\u{001B}\][^\u{0007}]*(\u{0007})/, with: "")
        sanitized = sanitized.replacing(/[\u{0000}-\u{0008}\u{000B}\u{000C}\u{000E}-\u{001F}\u{007F}]/, with: "")
        sanitized = stripHTMLOutsideCodeFences(sanitized)
        sanitized = sanitized.replacing(/\]\((?i:javascript|data|vbscript):[^)]*\)/) { _ in
            "](unsafe-link)"
        }
        return sanitized
    }

    private static func stripHTMLOutsideCodeFences(_ text: String) -> String {
        var inFence = false
        var fenceMarker = ""
        return text.components(separatedBy: .newlines).map { line in
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if trimmed.hasPrefix("```") || trimmed.hasPrefix("~~~") {
                let marker = String(trimmed.prefix(3))
                if !inFence {
                    inFence = true
                    fenceMarker = marker
                } else if marker == fenceMarker {
                    inFence = false
                    fenceMarker = ""
                }
                return line
            }
            guard !inFence else {
                return line
            }
            return line.replacing(/<\/?[A-Za-z][^>\n]*>/, with: "")
        }.joined(separator: "\n")
    }

    /// Turn long numbered link catalogs into a compact bullet list for chat.
    static func condenseVerboseSearchCatalog(_ text: String) -> String {
        let lines = text.components(separatedBy: .newlines)
        let linkLine = /^\d+\.\s+\[[^\]]+\]\([^)]+\)/
        var numberedLinkLines = 0
        for line in lines {
            let stripped = line.trimmingCharacters(in: .whitespaces)
            if stripped.firstMatch(of: linkLine) != nil {
                numberedLinkLines += 1
            }
        }
        guard numberedLinkLines >= 3 else {
            return text
        }

        let rowPattern = /^\d+\.\s+\[(?<title>[^\]]+)\]\((?<url>[^)]+)\)\s*-?\s*(?<blurb>.*)$/
        var output: [String] = []
        var converted = 0
        for line in lines {
            let stripped = line.trimmingCharacters(in: .whitespaces)
            if let match = stripped.firstMatch(of: rowPattern) {
                let title = String(match.title)
                let url = String(match.url)
                let blurb = String(match.blurb).trimmingCharacters(in: .whitespaces)
                if blurb.isEmpty {
                    output.append("- [\(title)](\(url))")
                } else {
                    output.append("- **\(title)** — \(blurb) [source](\(url))")
                }
                converted += 1
            } else {
                output.append(line)
            }
        }
        guard converted >= 3 else {
            return text
        }
        return output.joined(separator: "\n")
    }
}
