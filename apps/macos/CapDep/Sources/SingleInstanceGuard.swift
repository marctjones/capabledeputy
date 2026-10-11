import Darwin
import Foundation
import CryptoKit

final class SingleInstanceGuard {
    /// Separate daemon endpoints may be inspected by separate app instances.
    /// Keep the historical production lock so an older running app still wins.
    static func lockName(socketPath: String) -> String {
        if socketPath == "/tmp/capdep-\(getuid()).sock" { return "capdepmac" }
        let digest = SHA256.hash(data: Data(socketPath.utf8))
            .map { String(format: "%02x", $0) }.joined()
        return "capdepmac-\(digest.prefix(24))"
    }
    private let fileDescriptor: Int32
    let didAcquire: Bool

    init(name: String) {
        let path = "\(NSTemporaryDirectory())\(name).lock"
        fileDescriptor = open(path, O_CREAT | O_RDWR, S_IRUSR | S_IWUSR)
        if fileDescriptor < 0 {
            didAcquire = false
            return
        }
        didAcquire = flock(fileDescriptor, LOCK_EX | LOCK_NB) == 0
    }

    deinit {
        if fileDescriptor >= 0 {
            flock(fileDescriptor, LOCK_UN)
            close(fileDescriptor)
        }
    }
}
