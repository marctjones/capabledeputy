import CryptoKit
import Foundation

struct AppBuildIdentity: Equatable {
    let version: String
    let executableSHA256: String
    let sourceContentSHA256: String

    static func capture(bundle: Bundle = .main) -> AppBuildIdentity {
        let version = bundle.infoDictionary?["CFBundleShortVersionString"] as? String
            ?? CapDepBuildProvenance.packageVersion
        let executable = bundle.executableURL
        let sidecar = bundle.bundleURL
            .appendingPathComponent("Contents/MacOS/CapDepMac.bin")
        let binaryURL = FileManager.default.fileExists(atPath: sidecar.path) ? sidecar : executable
        return AppBuildIdentity(
            version: version,
            executableSHA256: binaryURL.flatMap(sha256(fileAt:)) ?? "",
            sourceContentSHA256: CapDepBuildProvenance.sourceContentSHA256,
        )
    }

    private static func sha256(fileAt url: URL) -> String? {
        guard let handle = try? FileHandle(forReadingFrom: url) else { return nil }
        defer { try? handle.close() }
        var hasher = SHA256()
        do {
            while let chunk = try handle.read(upToCount: 64 * 1024), !chunk.isEmpty {
                hasher.update(data: chunk)
            }
        } catch {
            return nil
        }
        return hasher.finalize().map { String(format: "%02x", $0) }.joined()
    }
}

struct DaemonBuildIdentity: Equatable, Sendable {
    let version: String
    let gitRevision: String
    let gitDirty: String
    let manifestHash: String
    let sourceContentHash: String

    init(dictionary: [String: Any]) {
        version = dictionary["version"] as? String ?? ""
        gitRevision = dictionary["git_rev"] as? String ?? ""
        gitDirty = dictionary["git_dirty"] as? String ?? ""
        manifestHash = dictionary["manifest_hash"] as? String ?? ""
        sourceContentHash = dictionary["source_content_hash"] as? String ?? ""
    }

    var revisionLabel: String {
        guard !gitRevision.isEmpty else { return "revision unavailable" }
        return String(gitRevision.prefix(12)) + (gitDirty == "dirty" ? " · modified" : "")
    }
}

enum BuildIdentityComparison: Equatable {
    case current
    case mismatch(String)
    case unknown(String)

    var title: String {
        switch self {
        case .current: "App and daemon source match this checkout"
        case .mismatch: "Build mismatch"
        case .unknown: "Comparison unavailable"
        }
    }

    var detail: String {
        switch self {
        case .current:
            "The running daemon's source content hash matches the available checkout."
        case .mismatch(let message), .unknown(let message):
            message
        }
    }

    var guidance: String? {
        switch self {
        case .current:
            nil
        case .mismatch:
            "Review pending edits and reviews, then rebuild and relaunch the affected component. CapDep will not restart the daemon or retry an effect for you."
        case .unknown:
            "A package version alone cannot establish freshness. Rebuild and relaunch from a reviewed source tree if you need to clear this uncertainty."
        }
    }

    static func evaluate(
        app: AppBuildIdentity,
        daemon: DaemonBuildIdentity?,
        expectedAppSourceHash: String?,
        expectedDaemonSourceHash: String?,
        connected: Bool,
    ) -> BuildIdentityComparison {
        guard connected else {
            return .unknown("The daemon is disconnected, so its running identity is unavailable.")
        }
        guard let daemon, !daemon.version.isEmpty else {
            return .unknown("The daemon did not provide a build identity.")
        }
        if !app.version.isEmpty, app.version != daemon.version {
            return .mismatch("The app bundle version (\(app.version)) differs from the daemon package version (\(daemon.version)).")
        }
        var unknownReasons: [String] = []
        if let expectedAppSourceHash, !expectedAppSourceHash.isEmpty {
            guard !app.sourceContentSHA256.isEmpty else {
                unknownReasons.append("The app did not embed source build provenance.")
                return .unknown(unknownReasons.joined(separator: " "))
            }
            guard app.sourceContentSHA256 == expectedAppSourceHash else {
                return .mismatch("The running app's Swift source content differs from the available checkout.")
            }
        } else {
            unknownReasons.append("No app source checkout is available for a content comparison.")
        }
        if let expectedDaemonSourceHash, !expectedDaemonSourceHash.isEmpty {
            guard !daemon.sourceContentHash.isEmpty else {
                return .unknown("The running daemon did not provide a source content hash.")
            }
            guard daemon.sourceContentHash == expectedDaemonSourceHash else {
                return .mismatch("The running daemon source content differs from the available checkout.")
            }
        } else {
            unknownReasons.append("No daemon source checkout is available for a content comparison.")
        }
        if !unknownReasons.isEmpty {
            return .unknown(unknownReasons.joined(separator: " "))
        }
        return .current
    }

    /// Hashes the Swift files used by the CapDepMac package target with the
    /// same path and byte framing as BuildIdentityGenerator.
    static func appSourceContentHash(packageRoot: URL) -> String? {
        let sourceRoot = packageRoot.appendingPathComponent("Sources", isDirectory: true)
        var isDirectory: ObjCBool = false
        guard FileManager.default.fileExists(atPath: sourceRoot.path, isDirectory: &isDirectory),
              isDirectory.boolValue,
              let enumerator = FileManager.default.enumerator(
                  at: sourceRoot,
                  includingPropertiesForKeys: [.isRegularFileKey],
                  options: [.skipsPackageDescendants],
              ) else {
            return nil
        }
        var files: [URL] = []
        for case let file as URL in enumerator where file.pathExtension == "swift" {
            files.append(file)
        }
        files.sort { $0.path < $1.path }
        return contentHash(files: files, relativeTo: packageRoot)
    }

    /// Matches capabledeputy's daemon source_content_hash framing exactly.
    static func daemonSourceContentHash(repositoryRoot: URL) -> String? {
        let sourceRoot = repositoryRoot.appendingPathComponent("src/capabledeputy", isDirectory: true)
        var isDirectory: ObjCBool = false
        guard FileManager.default.fileExists(atPath: sourceRoot.path, isDirectory: &isDirectory),
              isDirectory.boolValue,
              let enumerator = FileManager.default.enumerator(
                  at: sourceRoot,
                  includingPropertiesForKeys: [.isRegularFileKey],
                  options: [.skipsPackageDescendants],
              ) else {
            return nil
        }

        var files: [URL] = []
        for case let file as URL in enumerator where file.pathExtension == "py" {
            files.append(file)
        }
        files.sort { $0.path < $1.path }
        return contentHash(files: files, relativeTo: sourceRoot)
    }

    private static func contentHash(files: [URL], relativeTo root: URL) -> String? {
        guard !files.isEmpty else { return nil }
        let rootComponents = root.resolvingSymlinksInPath().standardizedFileURL.pathComponents
        var hasher = SHA256()
        for file in files {
            guard let data = try? Data(contentsOf: file) else { return nil }
            let fileComponents = file.resolvingSymlinksInPath().standardizedFileURL.pathComponents
            guard fileComponents.starts(with: rootComponents) else { return nil }
            let relativeName = fileComponents.dropFirst(rootComponents.count).joined(separator: "/")
            let name = Data(relativeName.utf8)
            hasher.update(data: lengthPrefix(name.count))
            hasher.update(data: name)
            hasher.update(data: lengthPrefix(data.count))
            hasher.update(data: data)
        }
        return hasher.finalize().map { String(format: "%02x", $0) }.joined()
    }

    private static func lengthPrefix(_ length: Int) -> Data {
        var value = UInt64(length).bigEndian
        return withUnsafeBytes(of: &value) { Data($0) }
    }
}

struct BuildReadiness: Equatable {
    let app: AppBuildIdentity
    let daemon: DaemonBuildIdentity?
    let comparison: BuildIdentityComparison

    static func make(
        app: AppBuildIdentity,
        daemon: DaemonBuildIdentity?,
        expectedAppSourceHash: String?,
        expectedDaemonSourceHash: String?,
        connected: Bool,
    ) -> BuildReadiness {
        return BuildReadiness(
            app: app,
            daemon: daemon,
            comparison: .evaluate(
                app: app,
                daemon: daemon,
                expectedAppSourceHash: expectedAppSourceHash,
                expectedDaemonSourceHash: expectedDaemonSourceHash,
                connected: connected,
            ),
        )
    }
}
