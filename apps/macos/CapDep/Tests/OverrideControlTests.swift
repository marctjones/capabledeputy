import XCTest
@testable import CapDepMac

/// #331 — pure-logic coverage for the GUI override control. GUI *behavior*
/// (banner → card → request/attest) needs a running app to verify; these pin
/// the parsing + RPC-marshalling logic that behavior rests on.
final class OverrideControlTests: XCTestCase {
    func testGrantParsesInvokerAndState() {
        let grant = OverrideGrantViewData(dictionary: [
            "id": "grant-1",
            "session_id": "sess-9",
            "action_kind": "SEND_EMAIL",
            "target": "boss@example.com",
            "state": "PENDING_ATTESTATION",
            "expires_at": "2026-07-18T12:00:00Z",
            "invoker_principal": "marc",
        ])
        XCTAssertEqual(grant.id, "grant-1")
        XCTAssertEqual(grant.sessionID, "sess-9")
        XCTAssertEqual(grant.actionKind, "SEND_EMAIL")
        XCTAssertEqual(grant.target, "boss@example.com")
        XCTAssertEqual(grant.invokerPrincipal, "marc")
        XCTAssertTrue(grant.awaitsAttestation)
    }

    func testGrantMissingFieldsDegradeGracefully() {
        let grant = OverrideGrantViewData(dictionary: ["state": "ACTIVE"])
        XCTAssertEqual(grant.sessionID, "")
        XCTAssertEqual(grant.invokerPrincipal, "")
        XCTAssertFalse(grant.id.isEmpty) // falls back to a UUID
        XCTAssertFalse(grant.awaitsAttestation)
    }

    func testRequestDraftMarshalsToOverrideRequestContract() {
        var draft = OverrideRequestDraft()
        draft.sessionID = "sess-9"
        draft.actionKind = "SEND_EMAIL"
        draft.target = "boss@example.com"
        draft.floor = "untrusted_never_egress"
        draft.invoker = "marc"
        draft.frictionConfirmed = true

        let params = draft.paramsDictionary()
        XCTAssertEqual(params["session_id"] as? String, "sess-9")
        XCTAssertEqual(params["action_kind"] as? String, "SEND_EMAIL")
        XCTAssertEqual(params["target"] as? String, "boss@example.com")
        XCTAssertEqual(params["floor"] as? String, "untrusted_never_egress")
        XCTAssertEqual(params["invoker"] as? String, "marc")
        XCTAssertEqual(params["friction_confirmed"] as? Bool, true)
        // Defaults present so the daemon never receives a missing category/tier.
        XCTAssertEqual(params["category"] as? String, "unknown")
        XCTAssertEqual(params["tier"] as? String, "restricted")
    }

    func testRequestDraftDefaultsFillEmptyCategoryTier() {
        var draft = OverrideRequestDraft()
        draft.category = ""
        draft.tier = ""
        let params = draft.paramsDictionary()
        XCTAssertEqual(params["category"] as? String, "unknown")
        XCTAssertEqual(params["tier"] as? String, "restricted")
    }

    func testRequestDraftSubmittableRequiresCoreFields() {
        var draft = OverrideRequestDraft()
        XCTAssertFalse(draft.isSubmittable)
        draft.sessionID = "s"
        draft.actionKind = "SEND_EMAIL"
        draft.floor = "untrusted_never_egress"
        XCTAssertFalse(draft.isSubmittable) // invoker still missing
        draft.invoker = "marc"
        XCTAssertTrue(draft.isSubmittable)
        draft.floor = "   "
        XCTAssertFalse(draft.isSubmittable) // whitespace-only is not enough
    }
}
