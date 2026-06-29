import Foundation

enum ChatImageURLResolver {
    enum Failure: Equatable, Error {
        case empty
        case unsupportedScheme
        case notFound
        case notAFile
        case tooLarge
        case unsupportedFormat
        case unreadable
    }

    struct ResolvedImage: Equatable {
        let url: URL
        let isAnimatedGIF: Bool
        let isRemote: Bool
    }

    static let maxBytes: Int64 = 4 * 1024 * 1024

    private static let supportedExtensions: Set<String> = [
        "png", "jpg", "jpeg", "gif", "tif", "tiff", "webp", "heic", "bmp",
    ]

    static func resolve(_ raw: String) -> Result<ResolvedImage, Failure> {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            return .failure(.empty)
        }

        if trimmed.hasPrefix("data:") {
            return .failure(.unsupportedScheme)
        }

        if trimmed.hasPrefix("https://") || trimmed.hasPrefix("http://") {
            guard let url = URL(string: trimmed),
                  supportedExtension(for: url) != nil else {
                return .failure(.unsupportedFormat)
            }
            return .success(
                ResolvedImage(url: url, isAnimatedGIF: isGIF(url), isRemote: true),
            )
        }

        let fileURL: URL
        if trimmed.hasPrefix("file://") {
            guard let url = URL(string: trimmed), url.isFileURL else {
                return .failure(.unsupportedScheme)
            }
            fileURL = url
        } else {
            let expanded = NSString(string: trimmed).expandingTildeInPath
            if expanded.hasPrefix("/") {
                fileURL = URL(fileURLWithPath: expanded)
            } else {
                let base = workingDirectory()
                fileURL = URL(fileURLWithPath: expanded, relativeTo: base).standardized
            }
        }

        return resolveLocalFile(fileURL)
    }

    static func matchesGrantPattern(path: String, pattern: String) -> Bool {
        let normalizedPath = NSString(string: path).expandingTildeInPath
        var normalizedPattern = pattern
        if normalizedPattern.hasSuffix("/*") {
            let prefix = String(normalizedPattern.dropLast(2))
            return normalizedPath == prefix || normalizedPath.hasPrefix(prefix + "/")
        }
        if normalizedPattern.hasSuffix("/") {
            normalizedPattern = String(normalizedPattern.dropLast())
        }
        return normalizedPath == normalizedPattern
            || normalizedPath.hasPrefix(normalizedPattern + "/")
    }

    private static func resolveLocalFile(_ url: URL) -> Result<ResolvedImage, Failure> {
        let path = url.path
        guard FileManager.default.fileExists(atPath: path) else {
            return .failure(.notFound)
        }
        var isDirectory: ObjCBool = false
        guard FileManager.default.fileExists(atPath: path, isDirectory: &isDirectory),
              !isDirectory.boolValue else {
            return .failure(.notAFile)
        }
        guard supportedExtension(for: url) != nil else {
            return .failure(.unsupportedFormat)
        }
        do {
            let values = try url.resourceValues(forKeys: [.fileSizeKey, .isReadableKey])
            if values.isReadable == false {
                return .failure(.unreadable)
            }
            if let size = values.fileSize, Int64(size) > maxBytes {
                return .failure(.tooLarge)
            }
        } catch {
            return .failure(.unreadable)
        }
        return .success(
            ResolvedImage(url: url, isAnimatedGIF: isGIF(url), isRemote: false),
        )
    }

    private static func workingDirectory() -> URL {
        if let repoRoot = ProcessInfo.processInfo.environment["CAPDEP_REPO_ROOT"],
           !repoRoot.isEmpty {
            return URL(fileURLWithPath: repoRoot, isDirectory: true)
        }
        return URL(fileURLWithPath: FileManager.default.currentDirectoryPath, isDirectory: true)
    }

    private static func supportedExtension(for url: URL) -> String? {
        let ext = url.pathExtension.lowercased()
        if ext.isEmpty {
            return nil
        }
        return supportedExtensions.contains(ext) ? ext : nil
    }

    private static func isGIF(_ url: URL) -> Bool {
        url.pathExtension.lowercased() == "gif"
    }
}