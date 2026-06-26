import XCTest
@testable import CapDepMac

final class DaemonContractModelTests: XCTestCase {
    func testSessionSecurityContextParsesDaemonProjection() {
        let context = SessionSecurityContext(dictionary: [
            "session": ["id": "session-1", "enforcement_mode": "enforce"],
            "labels": ["label_state": ["a": ["untrusted.external"]]],
            "capabilities": [
                "active": [["kind": "SEND_EMAIL"]],
                "used_kinds": ["READ_EMAIL"],
            ],
            "approvals": ["pending_count": 2],
            "policy": [
                "decision_count": 4,
                "deny_count": 1,
                "matched_rule_ids": ["rule.untrusted_egress"],
            ],
            "provenance": ["node_count": 3, "edge_count": 2],
            "actors": [
                "external_mcp": [["name": "gmail"]],
                "tools": [["name": "gmail.send"]],
                "onguard": ["client": ["client_id": "onguard.digest.daily"]],
            ],
            "security_models": [
                ["name": "information_flow_labels", "implemented": true, "evidence": ["label_count": 1]],
            ],
            "flow_patterns": [
                ["name": "human_approval_gate", "active": true, "evidence": ["approval_count": 2]],
            ],
            "limitations": ["No upstream MCP actor evidence is associated with this session."],
        ])

        XCTAssertEqual(context.sessionID, "session-1")
        XCTAssertEqual(context.enforcementMode, "enforce")
        XCTAssertEqual(context.labelCount, 1)
        XCTAssertEqual(context.activeCapabilityCount, 1)
        XCTAssertEqual(context.usedKinds, ["READ_EMAIL"])
        XCTAssertEqual(context.pendingApprovalCount, 2)
        XCTAssertEqual(context.policyDecisionCount, 4)
        XCTAssertEqual(context.policyDenyCount, 1)
        XCTAssertEqual(context.matchedRuleIDs, ["rule.untrusted_egress"])
        XCTAssertEqual(context.provenanceNodeCount, 3)
        XCTAssertEqual(context.provenanceEdgeCount, 2)
        XCTAssertEqual(context.externalMCPActors, ["gmail"])
        XCTAssertEqual(context.toolActors, ["gmail.send"])
        XCTAssertEqual(context.onguardClientID, "onguard.digest.daily")
        XCTAssertEqual(context.securityModels.first?.name, "information_flow_labels")
        XCTAssertEqual(context.securityModels.first?.active, true)
        XCTAssertEqual(context.flowPatterns.first?.name, "human_approval_gate")
        XCTAssertEqual(context.flowPatterns.first?.active, true)
        XCTAssertEqual(context.limitations.count, 1)
    }

    func testToolOutcomeParsesGrantRecoverySteps() {
        let outcome = ToolOutcome(dictionary: [
            "decision": "deny",
            "rule": "no-matching-capability",
            "reason": "no matching capability for READ_FS on /tmp/foo",
            "tool_name": "fs.read",
            "recovery_steps": [[
                "command": "/grant",
                "args": ["READ_FS", "/tmp/foo", "--one-shot"],
                "rationale": "Session lacks a capability for READ_FS on /tmp/foo.",
            ]],
        ])
        XCTAssertEqual(outcome.grantRecoveryStep?.grantKind, "READ_FS")
        XCTAssertEqual(outcome.grantRecoveryStep?.grantPattern, "/tmp/foo")
        XCTAssertTrue(outcome.grantRecoveryStep?.isOneShot == true)
    }

    func testOnguardDaemonModelsParseCoordinationState() {
        let client = OnguardClientViewData(dictionary: [
            "client_id": "onguard.finance.guard",
            "kind": "onguard",
            "status": "active",
            "owner": "operator",
        ])
        let command = OnguardCommandViewData(dictionary: [
            "command_id": "cmd-1",
            "client_id": "onguard.finance.guard",
            "command": "guard_finance_document",
            "status": "queued",
            "labels": ["external-untrusted", "finance"],
        ])
        let schedule = OnguardScheduleViewData(dictionary: [
            "schedule_id": "sched-1",
            "client_id": "onguard.finance.guard",
            "command": "guard_finance_document",
            "status": "approved",
            "next_run_at": "2026-06-22T09:00:00Z",
        ])
        let artifact = OnguardArtifactViewData(dictionary: [
            "artifact_id": "artifact-1",
            "client_id": "onguard.finance.guard",
            "artifact_type": "finance.quarantine",
            "status": "draft",
            "labels": ["finance"],
        ])
        let event = OnguardEventViewData(dictionary: [
            "event_id": "event-1",
            "client_id": "onguard.finance.guard",
            "event_type": "finance.quarantined",
            "acknowledged_by": "operator",
        ])
        let config = OnguardConfigViewData(dictionary: [
            "config_id": "config-1",
            "client_id": "onguard.finance.guard",
            "schema_name": "finance_guard",
            "status": "approved",
            "labels": ["finance"],
        ])

        XCTAssertEqual(client.id, "onguard.finance.guard")
        XCTAssertEqual(client.status, "active")
        XCTAssertEqual(command.command, "guard_finance_document")
        XCTAssertEqual(command.labels, ["external-untrusted", "finance"])
        XCTAssertEqual(schedule.nextRunAt, "2026-06-22T09:00:00Z")
        XCTAssertEqual(artifact.artifactType, "finance.quarantine")
        XCTAssertEqual(event.eventType, "finance.quarantined")
        XCTAssertEqual(event.acknowledgedBy, "operator")
        XCTAssertEqual(config.schemaName, "finance_guard")
        XCTAssertEqual(config.status, "approved")
    }
}
