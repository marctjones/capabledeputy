import Darwin
import Foundation

enum DaemonClientError: Error, LocalizedError {
    case connectFailed(String)
    case sendFailed
    case responseClosed
    case invalidResponse
    case daemonError(String)

    var errorDescription: String? {
        switch self {
        case .connectFailed(let path):
            return "daemon not running at \(path)"
        case .sendFailed:
            return "failed to write request to daemon"
        case .responseClosed:
            return "daemon closed the connection without a response"
        case .invalidResponse:
            return "daemon returned an invalid JSON-RPC response"
        case .daemonError(let message):
            return message
        }
    }
}

struct DaemonClient {
    let socketPath: String

    static func defaultSocketPath() -> String {
        if let override = ProcessInfo.processInfo.environment["CAPDEP_SOCKET"], !override.isEmpty {
            return override
        }
        if let runtimeDir = ProcessInfo.processInfo.environment["XDG_RUNTIME_DIR"], !runtimeDir.isEmpty {
            return "\(runtimeDir)/capdep.sock"
        }
        return "/tmp/capdep-\(getuid()).sock"
    }

    func call(method: String, params: [String: Any] = [:]) async throws -> Any {
        let request: [String: Any] = [
            "jsonrpc": "2.0",
            "method": method,
            "id": 1,
            "params": params,
        ]
        var encodedRequest = try JSONSerialization.data(withJSONObject: request, options: [])
        encodedRequest.append(0x0A)
        let requestData = encodedRequest

        return try await withCheckedThrowingContinuation { continuation in
            DispatchQueue.global(qos: .userInitiated).async {
                do {
                    continuation.resume(returning: try callSync(requestData: requestData))
                } catch {
                    continuation.resume(throwing: error)
                }
            }
        }
    }

    private func callSync(requestData payload: Data) throws -> Any {
        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else {
            throw DaemonClientError.connectFailed(socketPath)
        }
        defer {
            close(fd)
        }

        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        let encodedPath = Array(socketPath.utf8)
        guard encodedPath.count < MemoryLayout.size(ofValue: address.sun_path) else {
            throw DaemonClientError.connectFailed(socketPath)
        }
        withUnsafeMutableBytes(of: &address.sun_path) { rawBuffer in
            rawBuffer.initializeMemory(as: UInt8.self, repeating: 0)
            for (index, byte) in encodedPath.enumerated() {
                rawBuffer[index] = byte
            }
        }

        let connectResult = withUnsafePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { sockaddrPointer in
                Darwin.connect(fd, sockaddrPointer, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        guard connectResult == 0 else {
            throw DaemonClientError.connectFailed(socketPath)
        }

        try payload.withUnsafeBytes { rawBuffer in
            guard let baseAddress = rawBuffer.baseAddress else {
                throw DaemonClientError.sendFailed
            }
            var sent = 0
            while sent < payload.count {
                let written = Darwin.send(fd, baseAddress.advanced(by: sent), payload.count - sent, 0)
                guard written > 0 else {
                    throw DaemonClientError.sendFailed
                }
                sent += written
            }
        }

        var response = Data()
        var buffer = [UInt8](repeating: 0, count: 4096)
        while true {
            let count = Darwin.recv(fd, &buffer, buffer.count, 0)
            guard count > 0 else {
                throw DaemonClientError.responseClosed
            }
            if let newline = buffer[..<count].firstIndex(of: 0x0A) {
                response.append(buffer, count: newline)
                break
            }
            response.append(buffer, count: count)
        }

        guard
            let object = try JSONSerialization.jsonObject(with: response) as? [String: Any]
        else {
            throw DaemonClientError.invalidResponse
        }
        if let error = object["error"] as? [String: Any] {
            let message = error["message"] as? String ?? "unknown daemon error"
            throw DaemonClientError.daemonError(message)
        }
        guard let result = object["result"] else {
            throw DaemonClientError.invalidResponse
        }
        return result
    }
}
