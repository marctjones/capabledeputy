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
        XCTAssertEqual(
            outcome.grantRecoveryStep?.guiGrantPattern(),
            "/tmp/foo/*",
        )
    }

    func testApprovalDetailParsesReviewArtifact() {
        let detail = ApprovalDetail(dictionary: [
            "approval": ["id": 7, "action": "SEND_EMAIL", "target": "gmail:recipient:a@example.com"],
            "review_artifact": [
                "artifact_id": "artifact-1",
                "artifact_type": "email_draft",
                "title": "Reply draft",
                "target": "a@example.com",
                "destination_id": "gmail:recipient:a@example.com",
                "effect": "send",
                "content_type": "text/plain",
                "sha256": "abcdef1234567890",
                "labels": ["b": [["level": "external-untrusted"]]],
                "preview": "Hello",
                "preview_truncated": false,
            ],
            "effect_text": "Send an email",
            "plain_policy_reason": "requires approval",
        ])

        XCTAssertEqual(detail.reviewArtifact?.artifactType, "email_draft")
        XCTAssertEqual(detail.reviewArtifact?.destinationID, "gmail:recipient:a@example.com")
        XCTAssertEqual(detail.reviewArtifact?.shortHash, "abcdef123456")
        XCTAssertEqual(detail.reviewArtifact?.labels, ["external-untrusted"])
        XCTAssertEqual(detail.reviewArtifact?.preview, "Hello")
    }

    func testReviewArtifactDisplaysSafeScriptingTypes() {
        let script = ReviewArtifact(dictionary: [
            "artifact_id": "script-1",
            "artifact_type": "script",
            "title": "Rename photos",
            "target": "rename_photos.py",
            "destination_id": "script-workspace:photos:rename_photos.py",
            "effect": "create",
            "content_type": "text/x-python",
            "sha256": "abcdef1234567890",
            "preview": "print('rename')",
            "preview_truncated": false,
        ])
        let run = ReviewArtifact(dictionary: [
            "artifact_id": "run-1",
            "artifact_type": "script_run",
            "title": "Run rename photos",
            "destination_id": "script-workspace:photos:runs/run-1",
            "effect": "review_only",
            "content_type": "application/json",
        ])
        let export = ReviewArtifact(dictionary: [
            "artifact_id": "export-1",
            "artifact_type": "file_export",
            "title": "Export report",
            "destination_id": "script-workspace:photos:out/report.txt",
            "effect": "create",
        ])

        XCTAssertEqual(script.displayKind, "Script")
        XCTAssertEqual(script.systemImage, "chevron.left.forwardslash.chevron.right")
        XCTAssertEqual(run.displayKind, "Script Run")
        XCTAssertEqual(run.systemImage, "terminal")
        XCTAssertEqual(export.displayKind, "File Export")
        XCTAssertEqual(export.systemImage, "doc.badge.arrow.up")
    }

    func testChatPromptRunTracksQueuedRunningAndTerminalStates() {
        var run = ChatPromptRun(
            displayMessage: "Batch rename these files",
            daemonMessage: "/quality Batch rename these files",
            purpose: .general,
        )

        XCTAssertEqual(run.status, .queued)
        XCTAssertFalse(run.isTerminal)

        run.status = .running
        run.sessionID = "session-1"
        run.turnID = "turn-1"
        XCTAssertEqual(run.turnID, "turn-1")
        XCTAssertFalse(run.isTerminal)

        run.status = .completed
        XCTAssertTrue(run.isTerminal)

        run.status = .failed
        run.error = "daemon disconnected"
        XCTAssertTrue(run.isTerminal)
        XCTAssertEqual(run.error, "daemon disconnected")
    }

    func testRecoveryStepWidensFilePathToParentDirectory() {
        XCTAssertEqual(
            RecoveryStep.widenedGrantPattern(kind: "READ_FS", pattern: "/tmp/foo/bar.txt"),
            "/tmp/foo/*",
        )
        XCTAssertEqual(
            RecoveryStep.widenedGrantPattern(kind: "READ_FS", pattern: "/Volumes/External/"),
            "/Volumes/External/*",
        )
        XCTAssertEqual(
            RecoveryStep.widenedGrantPattern(kind: "SEND_EMAIL", pattern: "dad@example.com"),
            "dad@example.com",
        )
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

    func testOnguardNotificationModelParsesDaemonContract() {
        let notification = OnguardNotificationViewData(dictionary: [
            "id": "event-approval-needed",
            "class": "approval_needed",
            "urgency": "high",
            "title": "Approval needed",
            "body": "A queued action is waiting.",
            "deep_link": "capdep://onguard/event-approval-needed",
            "artifact_ref": "artifact:digest",
            "approval_id": 42,
        ])

        XCTAssertEqual(notification.id, "event-approval-needed")
        XCTAssertEqual(notification.notificationClass, "approval_needed")
        XCTAssertEqual(notification.urgency, "high")
        XCTAssertEqual(notification.deepLink, "capdep://onguard/event-approval-needed")
        XCTAssertEqual(notification.artifactRef, "artifact:digest")
        XCTAssertEqual(notification.approvalID, 42)
    }
}
