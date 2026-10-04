import Foundation

/// #319 — Swift mirror of the shared reconnect contract in
/// `src/capabledeputy/ipc/reconnect.py`. The daemon defines the reconnect
/// POLICY (docs/architecture.md: "recovery rules must remain daemon-side"); each
/// client consumes the same contract rather than reinventing backoff. Keep the
/// budgets + backoff formula here in lockstep with `reconnect.py`
/// (AMBIENT_RECONNECT / SEND_RECONNECT / TURN_RECOVERY_RECONNECT / `_MAX_BACKOFF`).
///
/// The macOS client legitimately owns only daemon *process-launch*; the retry
/// budgets, transient-vs-fatal classification, and backoff schedule come from
/// this shared contract.
enum ReconnectBudget {
    /// Status/list/navigation queries — ride out a transient bounce
    /// transparently, then surface (worst case ~5.1s).
    case ambient
    /// The user's explicit message-send — ONE sub-second retry so a socket-move
    /// / fast-restart blip recovers, while a real outage surfaces in ~150ms
    /// rather than hanging the UI on the full budget.
    case send
    /// An already-created turn lost its event stream. Do not replay the
    /// message: wait for a model-backed host daemon to report the terminal
    /// state (worst case ~31.1s).
    case turnRecovery

    var maxAttempts: Int {
        switch self {
        case .ambient: return 7
        case .send: return 2
        case .turnRecovery: return 20
        }
    }

    var baseDelay: Double {
        switch self {
        case .ambient, .turnRecovery: return 0.1
        case .send: return 0.15
        }
    }
}

enum ReconnectPolicy {
    /// Matches `_MAX_BACKOFF` in reconnect.py.
    static let maxBackoff = 2.0

    /// A transient daemon bounce (restart / socket-move) — safe to retry.
    /// Mirrors reconnect.py, which retries only `DaemonNotRunningError`: the
    /// "daemon isn't there right now" signals. A real RPC error
    /// (`daemonError`) or a malformed response is NOT transient and must
    /// propagate at once.
    static func isTransient(_ error: Error) -> Bool {
        guard let clientError = error as? DaemonClientError else {
            return false
        }
        switch clientError {
        case .connectFailed, .responseClosed:
            return true
        case .sendFailed, .invalidResponse, .daemonError:
            return false
        }
    }

    /// Turns live only for the daemon process lifetime.  A hard daemon
    /// restart can therefore reconnect successfully while the in-flight turn
    /// is no longer in its new lifecycle manager.  Treat only a daemon-issued
    /// missing-turn response as that safe, terminal condition.  In particular,
    /// do not mask policy, validation, or malformed-response errors: those
    /// remain visible failures.
    static func isLostInFlightTurn(_ error: Error) -> Bool {
        guard case let DaemonClientError.daemonError(message) = error else {
            return false
        }
        let normalized = message.lowercased()
        return normalized.contains("turn") && normalized.contains("not found")
    }

    /// Backoff before the retry that follows a failed attempt (0-based),
    /// matching reconnect.py: `min(base_delay * 2^attempt, MAX_BACKOFF)`.
    static func backoffSeconds(attempt: Int, budget: ReconnectBudget) -> Double {
        min(budget.baseDelay * pow(2.0, Double(attempt)), maxBackoff)
    }
}
