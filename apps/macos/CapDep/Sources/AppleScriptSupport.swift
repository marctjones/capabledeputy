import AppKit
import Foundation

// AppleScript scripting surface for CapDepMac (owner decision 2026-09-01,
// docs/adr-0001-applescript-ui-scripting.md): the UI is natively scriptable,
// in production builds included, so interactive flows can be driven by
// osascript-based automated test suites. Terminology lives in CapDep.sdef at
// the package root; both app bundles (the script-assembled dev bundle and the
// Xcode app shell) install it as Resources/CapDep.sdef and declare
// NSAppleScriptEnabled + OSAScriptingDefinition in Info.plist.
//
// Cocoa scripting resolves the classes below by their ObjC runtime names, so
// every class carries an explicit @objc(...) name that must match the
// `cocoa` elements in CapDep.sdef exactly.

/// App delegate that vends the scriptable application properties.
/// KVC keys must match the `cocoa key` attributes in CapDep.sdef.
@objc(CapDepScriptingDelegate)
@MainActor
public final class CapDepScriptingDelegate: NSObject, NSApplicationDelegate {
    nonisolated static let scriptingKeys: Set<String> = [
        "daemonConnected",
        "currentSessionID",
        "pendingApprovalIDs",
    ]

    public func application(_ sender: NSApplication, delegateHandlesKey key: String) -> Bool {
        Self.scriptingKeys.contains(key)
    }

    @objc var daemonConnected: Bool {
        CapDepAppModel.shared?.connected ?? false
    }

    @objc var currentSessionID: String {
        CapDepAppModel.shared?.currentSessionID ?? ""
    }

    @objc var pendingApprovalIDs: [Int] {
        CapDepAppModel.shared?.pendingApprovals.map(\.id) ?? []
    }
}

/// Async bridge shared by the script commands: Cocoa scripting calls
/// `performDefaultImplementation` synchronously on the main thread, while the
/// app model is async. Suspending the command and resuming it when the model
/// call finishes gives scripts synchronous semantics — an osascript line does
/// not return until the daemon round-trip completes, which is what makes
/// scripted interactive tests deterministic.
private func performSuspended(
    _ command: NSScriptCommand,
    _ operation: @escaping @MainActor () async -> Any?,
) -> Any? {
    // Cocoa scripting invokes commands on the main thread only, and the
    // resume happens back on the main actor, so the command never actually
    // leaves the main thread despite NSScriptCommand being non-Sendable.
    nonisolated(unsafe) let command = command
    MainActor.assumeIsolated {
        command.suspendExecution()
        Task { @MainActor in
            let result = await operation()
            command.resumeExecution(withResult: result)
        }
    }
    return nil
}

/// `send prompt "…" [in session "…"]` — sends a chat prompt, defaulting to
/// the current chat session. Returns whether the daemon accepted it.
@objc(CapDepSendPromptCommand)
final class CapDepSendPromptCommand: NSScriptCommand {
    override func performDefaultImplementation() -> Any? {
        guard let text = directParameter as? String, !text.isEmpty else {
            scriptErrorNumber = NSRequiredArgumentsMissingScriptError
            scriptErrorString = "send prompt requires non-empty prompt text."
            return nil
        }
        let requestedSession = evaluatedArguments?["inSession"] as? String
        return performSuspended(self) {
            guard let model = CapDepAppModel.shared else {
                return false
            }
            // An explicit `in session` targets exactly that id — fail if it's
            // invalid rather than silently substituting another session.
            // With no explicit target, go through `ensureSession` like the
            // normal chat-input path does: `currentSessionID` can be stale
            // (e.g. left over from a different daemon instance) and sending
            // straight to it fails deep inside the turn with a session-not-
            // found error instead of recovering.
            let sessionID: String
            if let requestedSession {
                sessionID = requestedSession
            } else if let session = await model.ensureSession(intent: text, purpose: .general) {
                sessionID = session.id
            } else {
                return false
            }
            guard !sessionID.isEmpty else {
                return false
            }
            return await model.send(message: text, sessionID: sessionID)
        }
    }
}

/// `approve approval <id>` — approves the pending approval with that id.
/// Returns false when the id is not (or no longer) pending.
@objc(CapDepApproveCommand)
final class CapDepApproveCommand: NSScriptCommand {
    override func performDefaultImplementation() -> Any? {
        guard let id = directParameter as? Int else {
            scriptErrorNumber = NSRequiredArgumentsMissingScriptError
            scriptErrorString = "approve approval requires an approval id."
            return nil
        }
        return performSuspended(self) {
            guard let model = CapDepAppModel.shared,
                  let approval = model.pendingApprovals.first(where: { $0.id == id })
            else {
                await CapDepAppModel.shared?.refresh()
                return false
            }
            await model.approve(approval)
            return true
        }
    }
}

/// `deny approval <id>` — denies the pending approval with that id.
/// Returns false when the id is not (or no longer) pending.
@objc(CapDepDenyCommand)
final class CapDepDenyCommand: NSScriptCommand {
    override func performDefaultImplementation() -> Any? {
        guard let id = directParameter as? Int else {
            scriptErrorNumber = NSRequiredArgumentsMissingScriptError
            scriptErrorString = "deny approval requires an approval id."
            return nil
        }
        return performSuspended(self) {
            guard let model = CapDepAppModel.shared,
                  model.pendingApprovals.contains(where: { $0.id == id })
            else {
                await CapDepAppModel.shared?.refresh()
                return false
            }
            await model.denyApprovalByID(id)
            return true
        }
    }
}

/// `refresh state` — re-pulls daemon state so subsequent property reads
/// (pending approval ids, daemon connected) are current.
@objc(CapDepRefreshCommand)
final class CapDepRefreshCommand: NSScriptCommand {
    override func performDefaultImplementation() -> Any? {
        performSuspended(self) {
            await CapDepAppModel.shared?.refresh()
            return true
        }
    }
}
