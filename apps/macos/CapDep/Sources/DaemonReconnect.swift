import Foundation

/// #319 — Swift mirror of the shared reconnect contract in
/// `src/capabledeputy/ipc/reconnect.py`. The daemon defines the reconnect
/// POLICY (docs/architecture.md: "recovery rules must remain daemon-side"); each
/// client consumes the same contract rather than reinventing backoff. Keep the
/// budgets + backoff formula here in lockstep with `reconnect.py`
/// (AMBIENT_RECONNECT / SEND_RECONNECT / `_MAX_BACKOFF`).
///
/// The macOS client legitimately owns only daemon *process-launch*; the retry
/// budgets, transient-vs-fatal classification, and backoff schedule come from
/// this shared contract.
enum ReconnectBudget {
    /// Status/list/navigation queries — ride out a transient bounce
    /// transparently, then surface (worst case ~3.1s).
    case ambient
    /// The user's explicit message-send — ONE sub-second retry so a socket-move
    /// / fast-restart blip recovers, while a real outage surfaces in ~150ms
    /// rather than hanging the UI on the full budget.
    case send

    var maxAttempts: Int {
        switch self {
        case .ambient: return 6
        case .send: return 2
        }
    }

    var baseDelay: Double {
        switch self {
        case .ambient: return 0.1
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

    /// Backoff before the retry that follows a failed attempt (0-based),
    /// matching reconnect.py: `min(base_delay * 2^attempt, MAX_BACKOFF)`.
    static func backoffSeconds(attempt: Int, budget: ReconnectBudget) -> Double {
        min(budget.baseDelay * pow(2.0, Double(attempt)), maxBackoff)
    }
}
