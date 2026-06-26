import Foundation

/// Normalizes assistant text for the chat surface: strips MLX artifacts and
/// renders lightweight markdown (links, emphasis, lists) as AttributedString.
enum ChatContentFormatter {
    static func displayText(_ raw: String) -> String {
        var text = stripMLXArtifacts(raw)
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