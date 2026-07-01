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
    static func blocks(
        from raw: String,
        isStreaming: Bool = false,
        authorizedImagePaths: Set<String> = [],
        holdUnverifiedGeneratedImages: Bool = false,
    ) -> [ChatBlock] {
        let cleaned = ChatContentFormatter.displayText(raw)
        guard !cleaned.isEmpty else {
            return []
        }
        let fencePattern = #/(?s)```([^`\n]*)\n(.*?)```/#
        var result: [ChatBlock] = []
        var cursor = cleaned.startIndex
        while let match = cleaned[cursor...].firstMatch(of: fencePattern) {
            if match.range.lowerBound > cursor {
                appendProse(
                    String(cleaned[cursor..<match.range.lowerBound]),
                    isStreaming: isStreaming,
                    authorizedImagePaths: authorizedImagePaths,
                    holdUnverifiedGeneratedImages: holdUnverifiedGeneratedImages,
                    to: &result,
                )
            }
            let language = String(match.1).trimmingCharacters(in: .whitespacesAndNewlines)
            let body = String(match.2).trimmingCharacters(in: .newlines)
            result.append(.code(language: language.isEmpty ? nil : language, body: body))
            cursor = match.range.upperBound
        }
        if cursor < cleaned.endIndex {
            appendProse(
                String(cleaned[cursor...]),
                isStreaming: isStreaming,
                authorizedImagePaths: authorizedImagePaths,
                holdUnverifiedGeneratedImages: holdUnverifiedGeneratedImages,
                to: &result,
            )
        }
        if result.isEmpty {
            appendProse(
                cleaned,
                isStreaming: isStreaming,
                authorizedImagePaths: authorizedImagePaths,
                holdUnverifiedGeneratedImages: holdUnverifiedGeneratedImages,
                to: &result,
            )
        }
        return result
    }

    static func isGeneratedWorkImagePath(_ path: String) -> Bool {
        let expanded = NSString(string: path).expandingTildeInPath
        return expanded.contains("/.capdep/work/images/")
    }

    static func shouldHoldGeneratedImage(
        _ path: String,
        authorizedImagePaths: Set<String>,
        holdUnverifiedGeneratedImages: Bool,
    ) -> Bool {
        guard holdUnverifiedGeneratedImages, isGeneratedWorkImagePath(path) else {
            return false
        }
        let expanded = NSString(string: path).expandingTildeInPath
        return !authorizedImagePaths.contains(expanded)
    }

    static func extractMarkdownImagePaths(from text: String) -> [String] {
        let pattern = /!\[[^\]]*\]\(([^)]+)\)/
        return text.matches(of: pattern).map { String($0.1) }
    }

    static func stripUnverifiedGeneratedImageMarkdown(
        from text: String,
        authorizedImagePaths: Set<String>,
    ) -> String {
        let pattern = /!\[[^\]]*\]\(([^)]+)\)/
        var cleaned = text
        for match in text.matches(of: pattern) {
            let path = String(match.1)
            if shouldHoldGeneratedImage(
                path,
                authorizedImagePaths: authorizedImagePaths,
                holdUnverifiedGeneratedImages: true,
            ) {
                cleaned = cleaned.replacingOccurrences(of: String(match.0), with: "")
            }
        }
        return cleaned.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// While streaming, hold a trailing incomplete `![alt](url` fragment as prose
    /// so the UI does not flicker between raw markdown and an image block.
    static func trailingIncompleteImageSuffix(in text: String) -> String? {
        guard let match = text.firstMatch(of: /(?s)(!\[[^\]]*(\]\([^)]*)?)$/) else {
            return nil
        }
        let suffix = String(match.1)
        return suffix.isEmpty ? nil : suffix
    }

    private static func appendProse(
        _ text: String,
        isStreaming: Bool,
        authorizedImagePaths: Set<String>,
        holdUnverifiedGeneratedImages: Bool,
        to result: inout [ChatBlock],
    ) {
        var trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            return
        }

        var holdTail = ""
        if isStreaming, let suffix = trailingIncompleteImageSuffix(in: trimmed) {
            holdTail = suffix
            trimmed = String(trimmed.dropLast(suffix.count))
                .trimmingCharacters(in: .whitespacesAndNewlines)
            if trimmed.isEmpty {
                if !holdTail.isEmpty {
                    result.append(.prose(holdTail))
                }
                return
            }
        }

        let imagePattern = /!\[(?<alt>[^\]]*)\]\((?<url>[^)]+)\)/
        var cursor = trimmed.startIndex
        var foundImage = false
        while let match = trimmed[cursor...].firstMatch(of: imagePattern) {
            foundImage = true
            if match.range.lowerBound > cursor {
                let prose = String(trimmed[cursor..<match.range.lowerBound])
                    .trimmingCharacters(in: .whitespacesAndNewlines)
                if !prose.isEmpty {
                    result.append(.prose(prose))
                }
            }
            let urlString = String(match.url)
            if shouldHoldGeneratedImage(
                urlString,
                authorizedImagePaths: authorizedImagePaths,
                holdUnverifiedGeneratedImages: holdUnverifiedGeneratedImages,
            ) {
                let placeholder = isStreaming ? "_Generating image…_" : "_Image pending…_"
                result.append(.prose(placeholder))
            } else {
                result.append(.image(alt: String(match.alt), urlString: urlString))
            }
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

        if !holdTail.isEmpty {
            if case .prose(let existing)? = result.last {
                result[result.count - 1] = .prose(existing + holdTail)
            } else {
                result.append(.prose(holdTail))
            }
        }
    }
}