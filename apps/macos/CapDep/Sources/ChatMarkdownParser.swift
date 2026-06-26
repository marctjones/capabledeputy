import Foundation

enum ChatBlock: Identifiable, Equatable {
    case prose(String)
    case code(language: String?, body: String)
    case image(alt: String, urlString: String)

    var id: String {
        switch self {
        case .prose(let text):
            return "prose-\(text.hashValue)"
        case .code(let language, let body):
            return "code-\(language ?? "")-\(body.hashValue)"
        case .image(let alt, let urlString):
            return "image-\(alt)-\(urlString)"
        }
    }
}

enum ChatMarkdownParser {
    static func blocks(from raw: String) -> [ChatBlock] {
        let cleaned = ChatContentFormatter.displayText(raw)
        guard !cleaned.isEmpty else {
            return []
        }
        let fencePattern = #/(?s)```([^`\n]*)\n(.*?)```/#
        var result: [ChatBlock] = []
        var cursor = cleaned.startIndex
        while let match = cleaned[cursor...].firstMatch(of: fencePattern) {
            if match.range.lowerBound > cursor {
                appendProse(String(cleaned[cursor..<match.range.lowerBound]), to: &result)
            }
            let language = String(match.1).trimmingCharacters(in: .whitespacesAndNewlines)
            let body = String(match.2).trimmingCharacters(in: .newlines)
            result.append(.code(language: language.isEmpty ? nil : language, body: body))
            cursor = match.range.upperBound
        }
        if cursor < cleaned.endIndex {
            appendProse(String(cleaned[cursor...]), to: &result)
        }
        if result.isEmpty {
            appendProse(cleaned, to: &result)
        }
        return result
    }

    private static func appendProse(_ text: String, to result: inout [ChatBlock]) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            return
        }
        let imagePattern = /!\[(?<alt>[^\]]*)\]\((?<url>[^)]+)\)/
        var cursor = trimmed.startIndex
        var foundImage = false
        while let match = trimmed[cursor...].firstMatch(of: imagePattern) {
            foundImage = true
            if match.range.lowerBound > cursor {
                let prose = String(trimmed[cursor..<match.range.lowerBound]).trimmingCharacters(in: .whitespacesAndNewlines)
                if !prose.isEmpty {
                    result.append(.prose(prose))
                }
            }
            result.append(.image(alt: String(match.alt), urlString: String(match.url)))
            cursor = match.range.upperBound
        }
        if foundImage {
            if cursor < trimmed.endIndex {
                let tail = String(trimmed[cursor...]).trimmingCharacters(in: .whitespacesAndNewlines)
                if !tail.isEmpty {
                    result.append(.prose(tail))
                }
            }
        } else {
            result.append(.prose(trimmed))
        }
    }
}