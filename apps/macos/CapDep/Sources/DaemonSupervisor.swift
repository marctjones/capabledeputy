import Foundation

enum DaemonSupervisorError: Error, LocalizedError {
    case commandFailed(String)
    case startTimedOut(String)

    var errorDescription: String? {
        switch self {
        case .commandFailed(let message):
            return message
        case .startTimedOut(let logPath):
            return "daemon failed to start; log: \(logPath)"
        }
    }
}

@MainActor
final class DaemonSupervisor {
    private var startedProcess: Process?
    private var daemonLogHandle: FileHandle?
    private var isEnsuring = false

    func ensureRunning(client: DaemonClient) async throws {
        if isEnsuring {
            return
        }
        isEnsuring = true
        defer {
            isEnsuring = false
        }

        if await canPing(client: client) {
            return
        }

        _ = try? await runLifecycleCommand(["daemon", "stop"], wait: true)
        try await startDaemon()
        try await waitForDaemon(client: client)
    }

    private func canPing(client: DaemonClient) async -> Bool {
        do {
            _ = try await client.call(method: "ping")
            return true
        } catch {
            return false
        }
    }

    private func waitForDaemon(client: DaemonClient) async throws {
        let logPath = daemonLogPath()
        for _ in 0..<150 {
            if await canPing(client: client) {
                return
            }
            try await Task.sleep(for: .milliseconds(200))
            if let process = startedProcess, !process.isRunning {
                throw DaemonSupervisorError.commandFailed("daemon exited early; log: \(logPath)")
            }
        }
        throw DaemonSupervisorError.startTimedOut(logPath)
    }

    private func startDaemon() async throws {
        let logPath = daemonLogPath()
        let process = try makeLifecycleProcess(["daemon", "start"])
        let logURL = URL(fileURLWithPath: logPath)
        FileManager.default.createFile(atPath: logPath, contents: nil)
        try? daemonLogHandle?.close()
        let logHandle = try FileHandle(forWritingTo: logURL)
        daemonLogHandle = logHandle
        process.standardOutput = logHandle
        process.standardError = logHandle
        try process.run()
        startedProcess = process
    }

    private func runLifecycleCommand(_ arguments: [String], wait: Bool) async throws -> Int32 {
        let process = try makeLifecycleProcess(arguments)
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        try process.run()
        if wait {
            process.waitUntilExit()
        }
        return process.terminationStatus
    }

    private func makeLifecycleProcess(_ arguments: [String]) throws -> Process {
        let repoRoot = repositoryRoot()
        let command = commandPrefix()
        let args = arguments.map(shellQuote).joined(separator: " ")
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/zsh")
        process.currentDirectoryURL = URL(fileURLWithPath: workingDirectory(repoRoot: repoRoot))
        process.arguments = [
            "-lc",
            "exec \(command) \(args)",
        ]
        return process
    }

    private func commandPrefix() -> String {
        if let override = ProcessInfo.processInfo.environment["CAPDEP_GUI_DAEMON_COMMAND"],
           !override.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        {
            return override
        }
        let local = "\(repositoryRoot())/.venv/bin/capdep"
        if canUseLocalCapDep(local) {
            return shellQuote(local)
        }
        return "capdep"
    }

    private func canUseLocalCapDep(_ path: String) -> Bool {
        let venvConfig = URL(fileURLWithPath: path)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("pyvenv.cfg")
            .path
        return FileManager.default.isExecutableFile(atPath: path)
            && FileManager.default.isReadableFile(atPath: venvConfig)
    }

    private func workingDirectory(repoRoot: String) -> String {
        if FileManager.default.isReadableFile(atPath: "\(repoRoot)/pyproject.toml") {
            return repoRoot
        }
        if let home = ProcessInfo.processInfo.environment["HOME"], !home.isEmpty {
            return home
        }
        return NSTemporaryDirectory()
    }

    private func daemonLogPath() -> String {
        let temp = NSTemporaryDirectory().trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        return "/\(temp)/capdep-gui-daemon.log"
    }

    private func repositoryRoot() -> String {
        if let override = ProcessInfo.processInfo.environment["CAPDEP_REPO_ROOT"],
           !override.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        {
            return override
        }
        let cwd = FileManager.default.currentDirectoryPath
        let candidate = URL(fileURLWithPath: cwd)
            .appendingPathComponent("../../..")
            .standardizedFileURL
            .path
        if FileManager.default.fileExists(atPath: "\(candidate)/pyproject.toml") {
            return candidate
        }
        return cwd
    }

    private func shellQuote(_ value: String) -> String {
        "'\(value.replacingOccurrences(of: "'", with: "'\\''"))'"
    }
}
