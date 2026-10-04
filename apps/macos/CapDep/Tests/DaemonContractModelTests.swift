import XCTest
@testable import CapDepMac

final class DaemonContractModelTests: XCTestCase {

    func testSessionArtifactOnlyTreatsDaemonGeneratedWorkImageAsRenderable() {
        let trusted = SessionArtifact(dictionary: [
            "artifact_id": "image-1",
            "kind": "generated_image",
            "source": "tool_output",
            "path": "/Users/example/.capdep/work/images/chart.png",
            "sha256": String(repeating: "a", count: 64),
        ])
        XCTAssertTrue(trusted.isAuthorizedGeneratedImage)

        let untrusted = SessionArtifact(dictionary: [
            "kind": "generated_image",
            "source": "planner_text",
            "path": "/Users/example/.capdep/work/images/chart.png",
            "sha256": String(repeating: "a", count: 64),
        ])
        XCTAssertFalse(untrusted.isAuthorizedGeneratedImage)
    }

    func testSessionArtifactWithholdsChangedBytes() throws {
        let file = FileManager.default.temporaryDirectory
            .appendingPathComponent("capdep-artifact-\(UUID().uuidString).png")
        defer { try? FileManager.default.removeItem(at: file) }
        try Data().write(to: file)
        let artifact = SessionArtifact(dictionary: [
            "kind": "generated_image",
            "source": "tool_output",
            "path": file.path,
            // SHA-256 for an empty file.
            "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        ])
        XCTAssertTrue(artifact.matchesRecordedDigest(at: file))

        try Data([0]).write(to: file)
        XCTAssertFalse(artifact.matchesRecordedDigest(at: file))
    }
    func testSessionSecurityContextParsesDaemonProjection() {
        let context = SessionSecurityContext(dictionary: [
            "session": ["id": "session-1", "enforcement_mode": "enforce"],
            "labels": [
                "label_state": ["a": ["untrusted.external"]],
                "propagation": [[
                    "audit_id": "label-event-1",
                    "source_tool": "web.fetch",
                    "categories_added": [],
                    "levels_added": ["external-untrusted"],
                ]],
            ],
            "capabilities": [
                "active": [[
                    "kind": "SEND_EMAIL",
                    "pattern": "recipient@example.com",
                    "origin": "operator_grant",
                    "expiry": "bounded",
                    "expires_at": "2026-10-05T00:00:00+00:00",
                    "max_amount": 1,
                    "revoked_by": ["approval_revoked"],
                ]],
                "used_kinds": ["READ_EMAIL"],
            ],
            "approvals": ["pending_count": 2],
            "policy": [
                "decision_count": 4,
                "deny_count": 1,
                "matched_rule_ids": ["rule.untrusted_egress"],
            ],
            "provenance": [
                "node_count": 3,
                "edge_count": 2,
                "node_offset": 100,
                "node_returned": 1,
                "node_has_more": true,
                "edge_returned": 1,
                "edge_has_more": true,
                "kind_filter": "tool_result",
                "nodes": [["id": "node-1", "kind": "tool_result", "materialized_id": "gmail-1"]],
                "edges": [["from": "node-0", "to": "node-1", "kind": "derived_from"]],
            ],
            "actors": [
                "external_mcp": [["name": "gmail"]],
                "tools": [["name": "gmail.send"]],
                "tool_surface": ["recent_selections": [[
                    "audit_id": "tool-surface-1",
                    "method": "capability_filtered",
                    "n_visible": 4,
                    "n_selected": 2,
                    "selected": ["gmail.messages.list", "web.fetch"],
                    "mandatory_added": ["web.fetch"],
                    "selection_reason": "intent match",
                ]]],
                "onguard": ["client": ["client_id": "onguard.digest.daily"]],
            ],
            "origin": ["kind": "workflow_template"],
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
        XCTAssertEqual(context.provenanceNodeOffset, 100)
        XCTAssertEqual(context.provenanceNodeReturned, 1)
        XCTAssertTrue(context.provenanceNodeHasMore)
        XCTAssertEqual(context.provenanceEdgeReturned, 1)
        XCTAssertTrue(context.provenanceEdgeHasMore)
        XCTAssertEqual(context.provenanceKindFilter, "tool_result")
        XCTAssertEqual(context.externalMCPActors, ["gmail"])
        XCTAssertEqual(context.toolActors, ["gmail.send"])
        XCTAssertEqual(context.onguardClientID, "onguard.digest.daily")
        XCTAssertEqual(context.originKind, "workflow_template")
        XCTAssertEqual(context.activeCapabilities.first?.kind, "SEND_EMAIL")
        XCTAssertEqual(context.activeCapabilities.first?.pattern, "recipient@example.com")
        XCTAssertEqual(context.activeCapabilities.first?.revokedBy, ["approval_revoked"])
        XCTAssertEqual(context.labelPropagation.first?.sourceTool, "web.fetch")
        XCTAssertEqual(context.labelPropagation.first?.levelsAdded, ["external-untrusted"])
        XCTAssertEqual(context.toolSurfaceSelections.first?.selected, ["gmail.messages.list", "web.fetch"])
        XCTAssertEqual(context.provenanceNodes.first?.materializedID, "gmail-1")
        XCTAssertEqual(context.provenanceEdges.first?.kind, "derived_from")
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
            // #421 — the daemon owns grant scope and already widened READ_FS to
            // the directory subtree; the client renders the pattern verbatim.
            "recovery_steps": [[
                "command": "/grant",
                "args": ["READ_FS", "/tmp/foo/*", "--one-shot"],
                "rationale": "Session lacks a capability for READ_FS on /tmp/foo/bar.txt.",
            ]],
        ])
        XCTAssertEqual(outcome.grantRecoveryStep?.grantKind, "READ_FS")
        XCTAssertEqual(outcome.grantRecoveryStep?.grantPattern, "/tmp/foo/*")
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

        run.status = .interrupted
        XCTAssertTrue(run.isTerminal)
        XCTAssertEqual(run.status.title, "Interrupted")

        run.status = .failed
        run.error = "daemon disconnected"
        XCTAssertTrue(run.isTerminal)
        XCTAssertEqual(run.error, "daemon disconnected")
    }

    func testDaemonConnectionHealthFindsMissingRequiredMethods() {
        let missing = DaemonConnectionHealth.missingRequiredMethods(
            available: ["ping", "version", "app.status"],
            required: ["app.status", "daemon.methods", "ping", "version"],
        )

        XCTAssertEqual(missing, ["daemon.methods"])

        let health = DaemonConnectionHealth(
            phase: .incompatible,
            version: "0.41.0",
            socketPath: "/tmp/capdep.sock",
            missingMethods: missing,
            detail: "missing daemon.methods",
        )
        XCTAssertFalse(health.isUsable)
        XCTAssertEqual(health.statusTitle, "Contract mismatch")
        XCTAssertEqual(health.phase.systemImage, "exclamationmark.shield.fill")
    }

    func testDaemonSettingsPreserveImageProfile() {
        var settings = DaemonSettings(dictionary: [
            "default_purpose": "research",
            "image_profile": "balanced",
            "prefer_local_mlx": false,
        ])

        XCTAssertEqual(settings.imageProfile, "balanced")
        XCTAssertEqual(settings.rpcDictionary["image_profile"] as? String, "balanced")

        settings.imageProfile = "quality"
        XCTAssertEqual(settings.rpcDictionary["image_profile"] as? String, "quality")
    }

    func testImageProfileAndReadinessParseDaemonPayloads() {
        let profile = ImageProfile(dictionary: [
            "id": "default",
            "title": "Default",
            "tier": "fast",
            "description": "Interactive default",
            "backend": "mflux",
            "model": "z-image-turbo",
            "steps": 9,
            "recommended": true,
        ])
        let readiness = ImageReadiness(dictionary: [
            "ok": false,
            "profile": "default",
            "backend": "mflux",
            "model": "z-image-turbo",
            "model_path": "filipstrand/Z-Image-Turbo-mflux-4bit",
            "device": "mps",
            "checks": [[
                "id": "mflux",
                "status": "error",
                "detail": "mflux not importable",
                "recovery": "Install image extras.",
            ]],
        ])

        XCTAssertEqual(profile.displayTitle, "Default (recommended)")
        XCTAssertEqual(profile.backend, "mflux")
        XCTAssertEqual(readiness.profile, "default")
        XCTAssertEqual(readiness.checks.first?.id, "mflux")
        XCTAssertEqual(readiness.checks.first?.recovery, "Install image extras.")
    }

    func testChatPromptRunCodablePreservesRecoveryFields() throws {
        let run = ChatPromptRun(
            id: "prompt-1",
            displayMessage: "Create a script",
            daemonMessage: "/tools Create a script",
            purpose: .research,
            sessionID: "session-1",
            turnID: "turn-1",
            status: .running,
            error: nil,
        )

        let data = try JSONEncoder().encode([run])
        let restored = try JSONDecoder().decode([ChatPromptRun].self, from: data)

        XCTAssertEqual(restored.first?.id, "prompt-1")
        XCTAssertEqual(restored.first?.purpose, .research)
        XCTAssertEqual(restored.first?.turnID, "turn-1")
        XCTAssertEqual(restored.first?.status, .running)
    }

    func testSessionModelReportPreservesDaemonResolvedIdentity() throws {
        let report = SessionModelReport(
            role: "planner.tools",
            model: "mlx-community/Qwen3-8B-4bit",
            reason: "large_tool_surface",
        )

        let data = try JSONEncoder().encode(report)
        let restored = try JSONDecoder().decode(SessionModelReport.self, from: data)

        XCTAssertEqual(restored.role, "planner.tools")
        XCTAssertEqual(restored.model, "mlx-community/Qwen3-8B-4bit")
        XCTAssertEqual(restored.reason, "large_tool_surface")
    }

    func testGuiGrantPatternRendersDaemonPatternVerbatim() {
        // #421 — the daemon owns grant scope (it already widened READ_FS to the
        // directory). The client renders the recovery-step pattern VERBATIM and
        // never widens/narrows it itself.
        let readFs = RecoveryStep(dictionary: ["command": "/grant", "args": ["READ_FS", "/tmp/foo/*"]])
        XCTAssertEqual(readFs.guiGrantPattern(), "/tmp/foo/*")
        let email = RecoveryStep(dictionary: ["command": "/grant", "args": ["SEND_EMAIL", "dad@example.com"]])
        XCTAssertEqual(email.guiGrantPattern(), "dad@example.com")
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
