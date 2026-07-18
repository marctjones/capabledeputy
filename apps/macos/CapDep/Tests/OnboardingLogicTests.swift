import XCTest
@testable import CapDepMac

/// #333 — the first-run onboarding decisions. GUI behavior (the wizard driving
/// daemon/config/connect to a working chat turn) needs a running-app
/// click-through; these pin the pure gating logic it rests on.
final class OnboardingLogicTests: XCTestCase {
    func testAutoPresentOnlyOnGenuineFirstRun() {
        // Fresh install, daemon not yet ready → show the wizard.
        XCTAssertTrue(OnboardingLogic.shouldAutoPresent(completed: false, planReady: false))
        // Already completed → never auto-present again.
        XCTAssertFalse(OnboardingLogic.shouldAutoPresent(completed: true, planReady: false))
        // Not completed but already fully ready (returning set-up user) → don't interrupt.
        XCTAssertFalse(OnboardingLogic.shouldAutoPresent(completed: false, planReady: true))
        XCTAssertFalse(OnboardingLogic.shouldAutoPresent(completed: true, planReady: true))
    }

    func testBlockingStepsRemainOnlyForNonOkBlockingSteps() {
        XCTAssertFalse(OnboardingLogic.blockingStepsRemain([]))

        let blockingNotOk = [step(status: "missing", blocking: true)]
        XCTAssertTrue(OnboardingLogic.blockingStepsRemain(blockingNotOk))

        let blockingOk = [step(status: "ok", blocking: true)]
        XCTAssertFalse(OnboardingLogic.blockingStepsRemain(blockingOk))

        // A non-blocking step that isn't ok does NOT hold up "start chatting".
        let nonBlockingNotOk = [step(status: "warn", blocking: false)]
        XCTAssertFalse(OnboardingLogic.blockingStepsRemain(nonBlockingNotOk))

        // Mixed: one satisfied blocking + one non-blocking issue → clear.
        let mixedClear = [step(status: "ok", blocking: true), step(status: "warn", blocking: false)]
        XCTAssertFalse(OnboardingLogic.blockingStepsRemain(mixedClear))

        // Mixed with a remaining blocking issue → not clear.
        let mixedBlocked = [step(status: "ok", blocking: true), step(status: "missing", blocking: true)]
        XCTAssertTrue(OnboardingLogic.blockingStepsRemain(mixedBlocked))
    }

    func testStatusMatchIsCaseInsensitive() {
        XCTAssertFalse(OnboardingLogic.blockingStepsRemain([step(status: "OK", blocking: true)]))
    }

    private func step(status: String, blocking: Bool) -> SetupPlanStep {
        SetupPlanStep(dictionary: ["title": "s", "status": status, "blocking": blocking])
    }
}
