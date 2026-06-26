import Foundation

/// Append-only chat debug log for CapDepMac (`~/Library/Logs/CapDep/chat-trace.log`).
enum ChatDebugLog {
    private static let queue = DispatchQueue(label: "local.capabledeputy.chat-debug-log")

    static var logURL: URL {
        let base = FileManager.default.urls(for: .libraryDirectory, in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSHomeDirectory()).appendingPathComponent("Library")
        return base
            .appendingPathComponent("Logs", isDirectory: true)
            .appendingPathComponent("CapDep", isDirectory: true)
            .appendingPathComponent("chat-trace.log", isDirectory: false)
    }

    static func log(_ message: String, metadata: [String: String] = [:]) {
        queue.async {
            do {
                let directory = logURL.deletingLastPathComponent()
                try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
                if !FileManager.default.fileExists(atPath: logURL.path) {
                    FileManager.default.createFile(atPath: logURL.path, contents: nil)
                }
                let handle = try FileHandle(forWritingTo: logURL)
                defer {
                    try? handle.close()
                }
                try handle.seekToEnd()

                let timestamp = ISO8601DateFormatter().string(from: Date())
                var line = "[\(timestamp)] \(message)"
                if !metadata.isEmpty {
                    let pairs = metadata.map { key, value in
                        let escaped = value
                            .replacingOccurrences(of: "\\", with: "\\\\")
                            .replacingOccurrences(of: "\"", with: "\\\"")
                        return "\(key)=\"\(escaped)\""
                    }.sorted()
                    line += " " + pairs.joined(separator: " ")
                }
                line += "\n"
                if let data = line.data(using: .utf8) {
                    try handle.write(contentsOf: data)
                }
            } catch {
                // Debug logging must never break chat.
            }
        }
    }
}