import SwiftUI

struct SessionEmailDraft: Identifiable {
    let id: String
    let version: Int
    let to: String
    let subject: String
    let body: String
    let sourceID: String
    let accountID: String
    let fromAddress: String
    let inReplyTo: String

    init?(_ raw: [String: Any]) {
        guard let id = raw["id"] as? String, let version = raw["version"] as? Int else { return nil }
        self.id = id
        self.version = version
        to = raw["to"] as? String ?? ""
        subject = raw["subject"] as? String ?? ""
        body = raw["body"] as? String ?? ""
        sourceID = raw["source_id"] as? String ?? ""
        accountID = raw["account_id"] as? String ?? ""
        fromAddress = raw["from_addr"] as? String ?? ""
        inReplyTo = raw["in_reply_to"] as? String ?? ""
    }
}

struct SessionEmailSource: Identifiable {
    let id: String
    let subject: String
    let sender: String
    let replyTo: String
    let accountID: String
    let truncated: Bool
    let bodyPresent: Bool
    let stale: Bool

    init?(_ raw: [String: Any]) {
        guard let id = raw["id"] as? String else { return nil }
        self.id = id
        subject = raw["subject"] as? String ?? "(no subject)"
        sender = raw["from"] as? String ?? ""
        replyTo = raw["reply_to"] as? String ?? ""
        accountID = raw["account_id"] as? String ?? ""
        truncated = raw["body_truncated"] as? Bool ?? true
        bodyPresent = raw["body_present"] as? Bool ?? false
        stale = raw["stale"] as? Bool ?? false
    }
}

struct EmailSourceSnapshot {
    let sources: [SessionEmailSource]
    let accountReady: Bool
    let readinessDetail: String

    init(_ raw: [String: Any]) {
        sources = (raw["sources"] as? [[String: Any]] ?? []).compactMap(SessionEmailSource.init)
        accountReady = raw["account_ready"] as? Bool ?? false
        readinessDetail = raw["readiness_detail"] as? String ?? "Sending account readiness is unavailable."
    }
}

struct EmailOperation: Identifiable {
    let id: String
    let status: String
    let subject: String
    let recipient: String
    let fromAddress: String
    let accountID: String
    let messageID: String
    let draftVersion: Int
    let detail: String
    let reconciliation: String
    let reconciliationReason: String

    init?(_ raw: [String: Any]) {
        guard let id = raw["id"] as? String else { return nil }
        self.id = id
        status = raw["status"] as? String ?? "unknown"
        let envelope = raw["envelope"] as? [String: Any] ?? [:]
        let message = envelope["message"] as? [String: Any] ?? envelope
        subject = message["subject"] as? String ?? raw["subject"] as? String ?? "Email reply"
        recipient = message["to"] as? String ?? raw["to"] as? String ?? ""
        fromAddress = message["from_addr"] as? String ?? raw["from_addr"] as? String ?? ""
        accountID = message["account_id"] as? String ?? raw["account_id"] as? String ?? ""
        messageID = message["message_id"] as? String ?? raw["message_id"] as? String ?? ""
        draftVersion = raw["draft_version"] as? Int ?? 0
        detail = raw["detail"] as? String ?? ""
        reconciliation = raw["reconciliation_outcome"] as? String ?? ""
        reconciliationReason = raw["reconciliation_reason"] as? String ?? ""
    }

    var statusText: String {
        switch status {
        case "smtp_accepted": return "SMTP accepted — recipient delivery unverified"
        case "outcome_unknown": return "Outcome unknown — do not resend without reconciliation"
        case "submitting": return "Submitting — awaiting SMTP outcome"
        case "failed_before_submission": return "Failed before submission — no SMTP acceptance"
        case "authorized": return "Authorized — not yet submitted"
        default: return status.replacingOccurrences(of: "_", with: " ")
        }
    }

    var identityText: String {
        "Account: \(accountID)\nFrom: \(fromAddress)\nTo: \(recipient)\nSubject: \(subject)\nMessage-ID: \(messageID)\nDraft version: \(draftVersion)\nOperation: \(id)"
    }
}

struct EmailDraftsView: View {
    @EnvironmentObject private var model: CapDepAppModel
    @Environment(\.dismiss) private var dismiss
    @Environment(\.capdepBackgroundDismiss) private var backgroundDismiss
    @State private var drafts: [SessionEmailDraft] = []
    @State private var sources: [SessionEmailSource] = []
    @State private var operations: [EmailOperation] = []
    @State private var editing: SessionEmailDraft?
    @State private var editorBlocksDismissal = false
    @State private var error = ""
    @State private var busy = false
    @State private var reconciling: EmailOperation?
    @State private var accountReady = false
    @State private var readinessDetail = ""
    let sessionID: String

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text(editing == nil ? "Email Replies" : "Edit Reply").font(.title2)
                Spacer()
                Button("Done") { dismissWorkspace() }.disabled(editorBlocksDismissal)
                    .accessibilityIdentifier("capdep.email.done")
            }
            Text("Drafts stay local. Each send requires review of its exact account, recipient and content.")
                .font(.caption).foregroundStyle(.secondary)
            if !error.isEmpty {
                Text(error).foregroundStyle(.red).textSelection(.enabled)
                    .accessibilityIdentifier("capdep.email.error")
            }
            if let draft = editing {
                EmailDraftEditor(draft: draft, sessionID: sessionID, stateChanged: { editorBlocksDismissal = $0 }, saved: { updated in
                    editing = updated
                }, reviewed: { approvalID in
                    model.presentApproval(id: approvalID)
                    dismissWorkspace()
                }, back: {
                    editorBlocksDismissal = false
                    editing = nil
                    Task { await reload() }
                })
                .id("\(draft.id)-\(draft.version)")
            } else {
                ScrollView {
                    VStack(alignment: .leading, spacing: 18) {
                        Text("Source Messages").font(.headline)
                        Text(readinessDetail).font(.caption).foregroundStyle(.secondary)
                        if sources.isEmpty && !busy {
                            Text("Ask for an inbox summary first. Only messages read in this session appear here.")
                                .foregroundStyle(.secondary)
                        }
                        ForEach(sources) { source in
                            VStack(alignment: .leading, spacing: 5) {
                                Text(source.subject).font(.headline)
                                Text("From: \(source.sender)").textSelection(.enabled)
                                Text("Account: \(source.accountID)").font(.caption)
                                if source.truncated || !source.bodyPresent {
                                    Text("Incomplete source — a complete message is required before replying.")
                                        .foregroundStyle(.orange)
                                }
                                if source.stale {
                                    Text("Mailbox identity changed — summarize the mailbox again before replying.")
                                        .foregroundStyle(.orange)
                                }
                                Button("Reply to \(source.subject)") {
                                    Task {
                                        busy = true
                                        do {
                                            editing = try await model.createEmailReply(sessionID: sessionID, sourceID: source.id)
                                            error = ""
                                        } catch { self.error = error.localizedDescription }
                                        busy = false
                                    }
                                }
                                .disabled(busy || !accountReady || source.truncated || !source.bodyPresent || source.stale)
                                .accessibilityIdentifier("capdep.email.reply.\(source.id)")
                            }
                            Divider()
                        }
                        Text("Saved Drafts").font(.headline)
                        if drafts.isEmpty && !busy { Text("No saved drafts in this session.").foregroundStyle(.secondary) }
                        ForEach(drafts) { draft in
                            Button {
                                editing = draft
                            } label: {
                                VStack(alignment: .leading, spacing: 4) {
                                    Text(draft.subject).font(.headline)
                                    Text("To: \(draft.to) · Version \(draft.version)").font(.caption)
                                }.frame(maxWidth: .infinity, alignment: .leading)
                            }
                            .accessibilityIdentifier("capdep.email.draft.\(draft.id)")
                        }
                        Text("Send History").font(.headline)
                        if operations.isEmpty && !busy { Text("No send operations in this session.").foregroundStyle(.secondary) }
                        ForEach(operations) { operation in
                            VStack(alignment: .leading, spacing: 4) {
                                Text(operation.subject).font(.headline)
                                Text(operation.recipient)
                                DisclosureGroup("Submission Identity") {
                                    Text(operation.identityText).font(.caption).textSelection(.enabled)
                                }
                                Text(operation.statusText).font(.callout)
                                    .accessibilityIdentifier("capdep.email.operation.\(operation.id)")
                                if !operation.detail.isEmpty { Text(operation.detail).font(.caption) }
                                if !operation.reconciliation.isEmpty {
                                    Text("Operator verification: \(operation.reconciliation.replacingOccurrences(of: "_", with: " "))")
                                        .font(.callout)
                                    Text(operation.reconciliationReason).font(.caption)
                                } else if operation.status == "outcome_unknown" {
                                    Button("Record Provider Verification") { reconciling = operation }
                                        .accessibilityIdentifier("capdep.email.reconcile.\(operation.id)")
                                }
                            }.textSelection(.enabled)
                        }
                    }.frame(maxWidth: .infinity, alignment: .leading)
                }
                Button("Reload") { Task { await reload() } }
                    .disabled(busy).accessibilityIdentifier("capdep.email.reload")
            }
            if busy { ProgressView().controlSize(.small) }
        }
        .padding(24).frame(minWidth: 620, idealWidth: 700, minHeight: 520, idealHeight: 660)
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Email reply workspace")
        .task { await reload() }
        .capdepBackgroundSafeSheet(item: $reconciling, title: "Verify Previous Submission") { operation in
            EmailReconciliationView(operation: operation, sessionID: sessionID) {
                reconciling = nil
                Task { await reload() }
            }.environmentObject(model)
        }
    }

    private func dismissWorkspace() {
        if let backgroundDismiss { backgroundDismiss.call() } else { dismiss() }
    }

    private func reload() async {
        busy = true
        do {
            let snapshot = try await model.loadEmailSources(sessionID: sessionID)
            sources = snapshot.sources
            accountReady = snapshot.accountReady
            readinessDetail = snapshot.readinessDetail
            drafts = try await model.loadEmailDrafts(sessionID: sessionID)
            operations = try await model.loadEmailOperations(sessionID: sessionID)
            error = ""
        } catch { self.error = error.localizedDescription }
        busy = false
    }
}

private struct EmailReconciliationView: View {
    @EnvironmentObject private var model: CapDepAppModel
    let operation: EmailOperation
    let sessionID: String
    let finished: () -> Void
    @State private var outcome = ""
    @State private var reason = ""
    @State private var confirmed = false
    @State private var busy = false
    @State private var error = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Verify the Previous Submission").font(.title2)
            Text(operation.identityText).font(.callout).textSelection(.enabled)
                .accessibilityIdentifier("capdep.email.reconciliation.identity")
            Text("Check the sending provider before recording a result. An absent inbox message alone does not prove that SMTP rejected it. This records your verification; it never sends or retries a message.")
            Picker("Verified result", selection: $outcome) {
                Text("Choose a verified result").tag("")
                Text("Provider accepted it").tag("smtp_accepted")
                    .accessibilityIdentifier("capdep.email.reconciliation.accepted")
                Text("Provider confirms it was not submitted").tag("confirmed_not_sent")
                    .accessibilityIdentifier("capdep.email.reconciliation.not-submitted")
            }
            .pickerStyle(.radioGroup)
            EmailTextField(title: "How did you verify the result?", text: $reason,
                           identifier: "capdep.email.reconciliation.reason", enabled: !busy)
            Toggle("I independently verified this result with the sending provider", isOn: $confirmed)
                .accessibilityIdentifier("capdep.email.reconciliation.confirm")
            if !error.isEmpty { Text(error).foregroundStyle(.red) }
            HStack {
                Button("Cancel", action: finished).disabled(busy)
                Spacer()
                Button("Record Verification") {
                    Task {
                        busy = true
                        do {
                            try await model.reconcileEmailOperation(sessionID: sessionID, operationID: operation.id, outcome: outcome, reason: reason)
                            finished()
                        } catch { self.error = error.localizedDescription }
                        busy = false
                    }
                }.disabled(busy || !confirmed || outcome.isEmpty || reason.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    .accessibilityIdentifier("capdep.email.reconciliation.record")
            }
        }.padding(24).frame(width: 540)
    }
}

private struct EmailDraftEditor: View {
    @EnvironmentObject private var model: CapDepAppModel
    let draft: SessionEmailDraft
    let sessionID: String
    let stateChanged: (Bool) -> Void
    let saved: (SessionEmailDraft) -> Void
    let reviewed: (Int) -> Void
    let back: () -> Void
    @State private var to = ""
    @State private var subject = ""
    @State private var content = ""
    @State private var sourceText = ""
    @State private var error = ""
    @State private var busy = false

    private var changed: Bool { to != draft.to || subject != draft.subject || content != draft.body }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("From: \(draft.fromAddress) · Account: \(draft.accountID)")
                .font(.caption).textSelection(.enabled)
            if !sourceText.isEmpty {
                DisclosureGroup("Original Message") {
                    ScrollView { Text(sourceText).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading) }
                        .frame(maxHeight: 140)
                }
            }
            EmailTextField(title: "To", text: $to, identifier: "capdep.email.to", enabled: !busy)
            EmailTextField(title: "Subject", text: $subject, identifier: "capdep.email.subject", enabled: !busy)
            Text("Reply").font(.headline)
            TextEditor(text: $content).font(.body)
                .disabled(busy)
                .frame(minHeight: 180)
                .overlay(RoundedRectangle(cornerRadius: 5).stroke(.quaternary))
                .accessibilityLabel("Reply body").accessibilityIdentifier("capdep.email.body")
            Text("Version \(draft.version)\(changed ? " · Unsaved changes" : " · Saved")")
                .font(.caption).foregroundStyle(.secondary)
                .accessibilityIdentifier("capdep.email.version")
            if !error.isEmpty { Text(error).foregroundStyle(.red).textSelection(.enabled) }
            HStack {
                Button("Back", action: back).disabled(busy || changed)
                Button("Discard Changes") {
                    to = draft.to; subject = draft.subject; content = draft.body
                }.disabled(busy || !changed)
                Spacer()
                Button("Save Changes") {
                    Task {
                        busy = true
                        do {
                            let updated = try await model.updateEmailDraft(sessionID: sessionID, draft: draft, to: to, subject: subject, body: content)
                            saved(updated)
                        } catch { self.error = error.localizedDescription }
                        busy = false
                    }
                }.disabled(busy || !changed).accessibilityIdentifier("capdep.email.save")
                Button("Review Saved Reply") {
                    Task {
                        busy = true
                        do { reviewed(try await model.requestEmailDraftReview(sessionID: sessionID, draft: draft)) }
                        catch { self.error = error.localizedDescription }
                        busy = false
                    }
                }
                .disabled(busy || changed || draft.body.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                .accessibilityIdentifier("capdep.email.review")
            }
        }
        .onChange(of: busy || changed, initial: true) { _, blocked in stateChanged(blocked) }
        .task {
            to = draft.to; subject = draft.subject; content = draft.body
            if !draft.sourceID.isEmpty {
                do { sourceText = try await model.loadEmailSourceBody(sessionID: sessionID, sourceID: draft.sourceID) }
                catch { self.error = error.localizedDescription }
            }
        }
    }
}
