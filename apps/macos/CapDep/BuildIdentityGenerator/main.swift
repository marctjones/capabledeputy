import CryptoKit
import Foundation

let arguments = Array(CommandLine.arguments.dropFirst())
guard arguments.count >= 2 else {
    fatalError("expected package root and output path")
}
let packageRoot = URL(fileURLWithPath: arguments[0], isDirectory: true).standardizedFileURL
let outputURL = URL(fileURLWithPath: arguments[1])
let inputURLs = arguments.dropFirst(2)
    .map { URL(fileURLWithPath: $0).standardizedFileURL }
    .sorted { $0.path < $1.path }

func lengthPrefix(_ length: Int) -> Data {
    var value = UInt64(length).bigEndian
    return withUnsafeBytes(of: &value) { Data($0) }
}

var hasher = SHA256()
for inputURL in inputURLs where inputURL.pathExtension == "swift" {
    guard let data = try? Data(contentsOf: inputURL) else {
        fatalError("cannot read build input \(inputURL.path)")
    }
    let relativePath = String(inputURL.path.dropFirst(packageRoot.path.count + 1))
        .replacingOccurrences(of: "\\", with: "/")
    let name = Data(relativePath.utf8)
    hasher.update(data: lengthPrefix(name.count))
    hasher.update(data: name)
    hasher.update(data: lengthPrefix(data.count))
    hasher.update(data: data)
}
let sourceHash = hasher.finalize().map { String(format: "%02x", $0) }.joined()

var packageVersion = ""
let versionURL = packageRoot.appendingPathComponent("../../../src/capabledeputy/version.py")
if let source = try? String(contentsOf: versionURL, encoding: .utf8),
   let match = source.range(of: #"__version__\s*=\s*['\"]([^'\"]+)['\"]"#, options: .regularExpression) {
    let assignment = String(source[match])
    packageVersion = assignment
        .split(whereSeparator: { $0 == "'" || $0 == "\"" })
        .last.map(String.init) ?? ""
}

let generated = """
// Generated from the SwiftPM target's declared source inputs. Do not edit.
enum CapDepBuildProvenance {
    static let packageVersion = \(String(reflecting: packageVersion))
    static let sourceContentSHA256 = \(String(reflecting: sourceHash))
    static let sourceFileCount = \(inputURLs.filter { $0.pathExtension == "swift" }.count)
}
"""
try FileManager.default.createDirectory(
    at: outputURL.deletingLastPathComponent(),
    withIntermediateDirectories: true,
)
try generated.write(to: outputURL, atomically: true, encoding: .utf8)
