import Darwin
import Foundation

final class SingleInstanceGuard {
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
