import XCTest
@testable import CapDepMac

/// #319 — the Swift reconnect contract must stay in lockstep with
/// `src/capabledeputy/ipc/reconnect.py`. Full mid-turn-restart behavior needs a
/// running daemon to verify; these pin the shared budgets, the transient-vs-fatal
/// classification, and the backoff schedule the wiring rests on.
final class ReconnectPolicyTests: XCTestCase {
    func testBudgetsMatchReconnectPy() {
        // AMBIENT_RECONNECT = {"max_attempts": 6, "base_delay": 0.1}
        XCTAssertEqual(ReconnectBudget.ambient.maxAttempts, 6)
        XCTAssertEqual(ReconnectBudget.ambient.baseDelay, 0.1, accuracy: 1e-9)
        // SEND_RECONNECT = {"max_attempts": 2, "base_delay": 0.15}
        XCTAssertEqual(ReconnectBudget.send.maxAttempts, 2)
        XCTAssertEqual(ReconnectBudget.send.baseDelay, 0.15, accuracy: 1e-9)
    }

    func testTransientClassificationMatchesDaemonNotRunning() {
        // Transient = "daemon isn't there right now" (restart / socket-move).
        XCTAssertTrue(ReconnectPolicy.isTransient(DaemonClientError.connectFailed("/tmp/s.sock")))
        XCTAssertTrue(ReconnectPolicy.isTransient(DaemonClientError.responseClosed))
        // Not transient: a real RPC error or a malformed/failed send.
        XCTAssertFalse(ReconnectPolicy.isTransient(DaemonClientError.daemonError("boom")))
        XCTAssertFalse(ReconnectPolicy.isTransient(DaemonClientError.invalidResponse))
        XCTAssertFalse(ReconnectPolicy.isTransient(DaemonClientError.sendFailed))
        // Not a DaemonClientError at all.
        XCTAssertFalse(ReconnectPolicy.isTransient(NSError(domain: "x", code: 1)))
    }

    func testBackoffMatchesExponentialFormula() {
        // reconnect.py: min(base_delay * 2^attempt, MAX_BACKOFF), MAX_BACKOFF = 2.0
        XCTAssertEqual(ReconnectPolicy.backoffSeconds(attempt: 0, budget: .ambient), 0.1, accuracy: 1e-9)
        XCTAssertEqual(ReconnectPolicy.backoffSeconds(attempt: 1, budget: .ambient), 0.2, accuracy: 1e-9)
        XCTAssertEqual(ReconnectPolicy.backoffSeconds(attempt: 2, budget: .ambient), 0.4, accuracy: 1e-9)
        XCTAssertEqual(ReconnectPolicy.backoffSeconds(attempt: 0, budget: .send), 0.15, accuracy: 1e-9)
        XCTAssertEqual(ReconnectPolicy.backoffSeconds(attempt: 1, budget: .send), 0.3, accuracy: 1e-9)
        // Capped at MAX_BACKOFF.
        XCTAssertEqual(ReconnectPolicy.backoffSeconds(attempt: 20, budget: .ambient), 2.0, accuracy: 1e-9)
    }
}
