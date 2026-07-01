import Foundation

enum ChatImageAttachment {
    static func markdownSnippet(alt: String, path: String) -> String? {
        let trimmedPath = path.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmedPath.isEmpty else {
            return nil
        }
        return "![\(alt)](\(trimmedPath))"
    }

    static func appendSnippet(_ snippet: String, to content: String) -> String? {
        guard !snippet.isEmpty else {
            return nil
        }
        if let path = pathFromImageSnippet(snippet), content.contains("](\(path))") {
            return nil
        }
        if content.isEmpty {
            return snippet
        }
        return content + "\n\n" + snippet
    }

    static func preserveImageSnippets(from streamedContent: String, in finalContent: String) -> String {
        var merged = finalContent
        for snippet in imageSnippets(in: streamedContent) {
            if let updated = appendSnippet(snippet, to: merged) {
                merged = updated
            }
        }
        return merged
    }

    static func pathFromImageSnippet(_ snippet: String) -> String? {
        guard let match = snippet.firstMatch(of: /!\[[^\]]*\]\((?<path>[^)]+)\)/) else {
            return nil
        }
        return String(match.path)
    }

    private static func imageSnippets(in content: String) -> [String] {
        content.matches(of: /!\[[^\]]*\]\([^)]+\)/).map { String($0.output) }
    }
}
