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

    static func pathFromImageSnippet(_ snippet: String) -> String? {
        guard let match = snippet.firstMatch(of: /!\[[^\]]*\]\((?<path>[^)]+)\)/) else {
            return nil
        }
        return String(match.path)
    }
}