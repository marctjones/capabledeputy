import SwiftUI

struct TaskApprovalCards: View {
    @EnvironmentObject private var model: CapDepAppModel
    let sessionID: String
    var body: some View {
        ForEach(model.visibleTaskApprovals(sessionID: sessionID)) { item in
            VStack(alignment: .leading, spacing: 10) {
                Text(item.title).font(.headline).foregroundStyle(item.canApprove ? .orange : .primary)
                Text(item.approval.actionTitle).font(.title3).textSelection(.enabled)
                if let email = item.email { Text(email.subject).foregroundStyle(.secondary) }
                Text(item.explanation).font(.callout)
                Button(item.canApprove ? "Review and send" : "View reply and status") {
                    model.presentApproval(id: item.id)
                }.buttonStyle(.borderedProminent)
                    .accessibilityIdentifier("capdep.task-review.open.\(item.id)")
            }
            .padding(16).frame(maxWidth: .infinity, alignment: .leading)
            .background(.background, in: RoundedRectangle(cornerRadius: 10))
            .overlay(alignment: .leading) { RoundedRectangle(cornerRadius: 2).fill(item.canApprove ? Color.orange : Color.secondary).frame(width: 3) }
            .accessibilityElement(children: .contain)
            .accessibilityIdentifier("capdep.task-review.card.\(item.id)")
        }
    }
}

struct TaskApprovalQueueView: View {
    @EnvironmentObject private var model: CapDepAppModel
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Button("Back to conversation") { model.showingApprovalQueue = false }
            Text("Approvals").font(.largeTitle)
            if model.pendingApprovals.isEmpty { Text("No actions awaiting your decision.").foregroundStyle(.secondary) }
            List(model.pendingApprovals) { approval in
                Button { model.presentApproval(id: approval.id) } label: {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(approval.actionTitle).font(.headline)
                        Text(model.sessions.first(where: { $0.id == approval.fromSession })?.intent ?? "Conversation")
                            .font(.callout).foregroundStyle(.secondary)
                        if let email = EmailReviewPresentation(payload: approval.payload) { Text(email.subject).font(.callout) }
                    }.padding(.vertical, 6).frame(maxWidth: .infinity, alignment: .leading)
                }.buttonStyle(.plain)
            }
        }.padding(24).background(.background)
    }
}

struct TaskApprovalReviewView: View {
    @EnvironmentObject private var model: CapDepAppModel
    let approvalID: Int
    @State private var editing: SessionEmailDraft?
    @State private var to = ""
    @State private var subject = ""
    @State private var reply = ""
    @State private var source = ""
    @State private var sourceMetadata = ""
    @State private var sourceRecipient = ""
    @State private var error = ""
    @State private var busy = false
    @State private var confirmLeaving = false
    @State private var reconciling = false
    @State private var evidence = ""
    @AccessibilityFocusState private var reviewFocused: Bool

    private var approval: Approval? { model.allApprovals.first { $0.id == approvalID } }
    private var item: TaskApproval? {
        guard let approval else { return nil }
        return model.taskApprovals[approval.fromSession]?.first { $0.id == approvalID }
    }
    private var changed: Bool { guard let editing else { return false }; return to != editing.to || subject != editing.subject || reply != editing.body }
    private var deciding: Bool { model.decidingApprovalIDs.contains(approvalID) }
    private var connected: Bool {
        model.connected && approval.map { model.taskApprovalErrors[$0.fromSession] == nil } == true
    }

    private var heading: String {
        editing != nil ? "Edit reply" : deciding ? "Sending reply…" : item?.canApprove == true ? "Send this reply?" : item?.title ?? "Loading review"
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Button("Back to conversation") { leave() }
                    .accessibilityIdentifier("capdep.task-review.back")
                Spacer()
                if let approval {
                    Text(model.sessions.first(where: { $0.id == approval.fromSession })?.intent ?? "Original task")
                        .font(.callout).foregroundStyle(.secondary).lineLimit(2)
                }
            }.padding(20)
            Divider()
            if let item {
                if let email = item.email, editing == nil {
                    VStack(alignment: .leading, spacing: 8) {
                        field("From", email.from + " · " + email.account)
                        field("To", email.to + (email.to.caseInsensitiveCompare(email.from) == .orderedSame ? " · Yourself" : ""))
                        field("Subject", email.subject)
                        if !sourceRecipient.isEmpty && sourceRecipient.caseInsensitiveCompare(email.to) != .orderedSame {
                            Text("Recipient differs from the original reply address: " + sourceRecipient).font(.callout).foregroundStyle(.orange)
                        }
                    }.padding(.horizontal, 24).padding(.vertical, 12)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .accessibilityElement(children: .contain)
                        .accessibilityIdentifier("capdep.email.review-envelope")
                    Divider()
                }
                ScrollView {
                    VStack(alignment: .leading, spacing: 18) {
                        Text(heading)
                            .font(.system(.largeTitle, design: .serif)).accessibilityAddTraits(.isHeader).accessibilityFocused($reviewFocused)
                            .accessibilityLabel(heading).accessibilityValue(heading)
                            .accessibilityIdentifier("capdep.task-review.title")
                        Text(editing != nil ? "The previous review has been withdrawn. Saving prepares a new review." : item.explanation)
                            .foregroundStyle(.secondary)
                        if editing == nil && !model.changedReplyFields(item).isEmpty {
                            Text(model.changedReplyFields(item)).font(.callout).foregroundStyle(.orange)
                        }
                        if !connected {
                            Label("Connection unavailable. Reconnect to check this review before sending.", systemImage: "wifi.exclamationmark")
                            Button("Reconnect and check") { Task { await model.refresh(); await model.refreshTaskApprovals(sessionID: item.approval.fromSession) } }
                        }
                        if !error.isEmpty { Text(error).foregroundStyle(.red).textSelection(.enabled).accessibilityIdentifier("capdep.task-review.error") }
                        if !item.error.isEmpty { Text(item.error).foregroundStyle(.orange).textSelection(.enabled) }
                        if let email = item.email {
                            if let editing {
                                Text("From \(editing.fromAddress) · \(editing.accountID)").textSelection(.enabled)
                                EmailTextField(title: "To", text: $to, identifier: "capdep.task-review.to", enabled: !busy)
                                EmailTextField(title: "Subject", text: $subject, identifier: "capdep.task-review.subject", enabled: !busy)
                                Text("Reply").font(.headline)
                                TextEditor(text: $reply).font(.body).frame(minHeight: 200)
                                    .accessibilityLabel("Reply body").accessibilityIdentifier("capdep.task-review.body-editor")
                            } else {
                                VStack(alignment: .leading, spacing: 14) {
                                    Text(email.body).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
                                        .accessibilityIdentifier("capdep.task-review.body")
                                    Text("1 recipient · No attachments · Plain-text reply").font(.caption).foregroundStyle(.secondary)
                                }.padding(20).frame(maxWidth: .infinity, alignment: .leading)
                                    .background(.quaternary.opacity(0.3), in: RoundedRectangle(cornerRadius: 8))
                                if !email.isBound { Text("This reply cannot be fully reviewed here. Prepare a new reply from its source message.").foregroundStyle(.orange) }
                            }
                            DisclosureGroup("Original message") {
                                if source.isEmpty {
                                    Button("Load retained source message") { Task { await loadSource(item, email: email) } }
                                } else {
                                    Text("Retained snapshot; not a live mailbox check.").font(.caption).foregroundStyle(.secondary)
                                    Text(sourceMetadata).font(.callout).textSelection(.enabled)
                                    Text(source).textSelection(.enabled)
                                }
                            }
                        } else {
                            NonEmailApprovalReviewSection(review: NonEmailApprovalReview(approval: item.approval))
                        }
                        if reconciling {
                            VStack(alignment: .leading, spacing: 12) {
                                Text("Record provider evidence").font(.headline)
                                Text("A missing inbox message does not prove this reply was not sent. Record what the provider confirmed for this exact message.")
                                TextEditor(text: $evidence).frame(minHeight: 100).accessibilityLabel("Provider evidence")
                                ViewThatFits(in: .horizontal) {
                                    HStack { reconciliationButtons(item) }
                                    VStack(alignment: .leading) { reconciliationButtons(item) }
                                }
                            }
                        }
                        DisclosureGroup("Technical details") {
                            Text("Review \(item.id)\nState: \(item.state)\nExpires: \(item.approval.expiresAt)\nOperation: \(item.operationID)\n\(item.operationDetail)")
                                .font(.caption.monospaced()).textSelection(.enabled)
                            Text(item.approval.payload).font(.caption.monospaced()).textSelection(.enabled)
                        }
                    }.padding(24).frame(maxWidth: .infinity, alignment: .leading)
                }
                Divider()
                VStack(alignment: .leading, spacing: 12) {
                    if editing != nil { Text("Nothing is sent when you save.").font(.callout) }
                    else if let email = item.email, item.canApprove {
                        Text("Sends this message from \(email.from) to \(email.to). This message only. You cannot undo sending.").font(.callout)
                        if let expiry = ISO8601DateFormatter().date(from: item.approval.expiresAt) {
                            Text("Review available until \(expiry.formatted(date: .omitted, time: .shortened))").font(.caption)
                        }
                    }
                    if busy || deciding { ProgressView(deciding ? "Submitting this reply…" : "Updating review…") }
                    ViewThatFits(in: .horizontal) {
                        HStack { actions(item); Spacer(minLength: 0) }
                        VStack(alignment: .leading, spacing: 10) { actions(item) }
                    }.disabled(busy || deciding)
                }.padding(20).background(.background)
            } else {
                ContentUnavailableView("Loading action review", systemImage: "envelope", description: Text("Your conversation stays available. Reconnect if the review cannot be loaded."))
                Button("Check again") { Task { await model.refresh() } }.padding()
            }
        }.background(.background)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("capdep.task-review.workspace")
        .task(id: approvalID) {
            if let saved = model.replyEditorState, saved.approvalID == approvalID {
                editing = saved.draft; to = saved.to; subject = saved.subject; reply = saved.body
            }
            if let approval { await model.refreshTaskApprovals(sessionID: approval.fromSession) }
            reviewFocused = true
            if let item, let email = item.email { await loadSource(item, email: email, reportError: false) }
        }
        .onChange(of: to) { _, _ in retainEdits() }
        .onChange(of: subject) { _, _ in retainEdits() }
        .onChange(of: reply) { _, _ in retainEdits() }
        .confirmationDialog("Save your draft before leaving?", isPresented: $confirmLeaving) {
            Button("Save draft") { Task { await save(leaveAfter: true) } }
            Button("Discard changes", role: .destructive) { editing = nil; model.replyEditorState = nil; model.approvalWindowID = nil }
            Button("Keep editing", role: .cancel) {}
        }
        .onExitCommand { leave() }
    }

    private func field(_ name: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 3) { Text(name).font(.caption).foregroundStyle(.secondary); Text(value).textSelection(.enabled).accessibilityIdentifier("capdep.task-review.field.\(name.lowercased())") }
    }

    @ViewBuilder private func actions(_ item: TaskApproval) -> some View {
        if editing != nil {
            Button("Discard edits") { Task { await discard(item) } }
            Button("Save and review") { Task { await save(leaveAfter: false) } }.buttonStyle(.borderedProminent)
                .accessibilityIdentifier("capdep.task-review.save")
        } else if item.canApprove {
            if item.email != nil { Button("Edit reply") { Task { await beginEdit(item) } }.disabled(!connected).accessibilityIdentifier("capdep.task-review.edit") }
            Button(item.email == nil ? "Don't proceed" : "Don't send") { Task { await model.deny(item.approval); error = model.lastError ?? ""; await model.refreshTaskApprovals(sessionID: item.approval.fromSession) } }
                .disabled(!connected).accessibilityIdentifier("capdep.email.deny")
            Button(item.approval.touchIDPolicyEnabled && item.approval.requiresStrongAuth ? "Authenticate and send" : "Send this reply") {
                Task { await model.approve(item.approval); error = model.lastError ?? "" }
            }.buttonStyle(.borderedProminent).disabled(!connected || item.email?.isBound != true)
                .accessibilityIdentifier("capdep.email.approve")
        } else if item.permitsFreshReview && item.email != nil {
            Button("Prepare fresh review") { Task { await fresh(item) } }.disabled(!connected).buttonStyle(.borderedProminent).accessibilityIdentifier("capdep.task-review.fresh")
        } else if item.state == "outcome_unknown" {
            Button("Check status") { Task { await model.refreshTaskApprovals(sessionID: item.approval.fromSession) } }
            Button("Record provider evidence") { reconciling = true }.disabled(!connected)
        } else {
            Button("Back to conversation") { leave() }
            Button("Check status") { Task { await model.refreshTaskApprovals(sessionID: item.approval.fromSession) } }
        }
    }
    @ViewBuilder private func reconciliationButtons(_ item: TaskApproval) -> some View {
        Button("Provider confirms not sent") { Task { await reconcile(item, outcome: "confirmed_not_sent") } }.disabled(evidence.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || busy)
        Button("Provider confirms acceptance") { Task { await reconcile(item, outcome: "smtp_accepted") } }.disabled(evidence.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || busy)
    }
    private func retainEdits() {
        guard let editing else { return }
        model.replyEditorState = ReplyEditorState(approvalID: approvalID, draft: editing, to: to, subject: subject, body: reply)
    }
    private func leave() { if busy { return }; if changed { confirmLeaving = true } else { model.replyEditorState = nil; model.approvalWindowID = nil } }
    private func beginEdit(_ item: TaskApproval) async {
        busy = true; defer { busy = false }
        do { let draft = try await model.beginEditingReply(item); editing = draft; to = draft.to; subject = draft.subject; reply = draft.body; retainEdits(); error = "" }
        catch { self.error = error.localizedDescription }
    }
    private func save(leaveAfter: Bool) async {
        guard let editing, let item else { return }
        busy = true; defer { busy = false }
        do {
            let updated = try await model.updateEmailDraft(sessionID: item.approval.fromSession, draft: editing, to: to, subject: subject, body: reply)
            self.editing = updated; retainEdits() // Preserve a successful save if the following review request fails.
            if leaveAfter { self.editing = nil; model.replyEditorState = nil; model.approvalWindowID = nil; return }
            let id = try await model.requestEmailDraftReview(sessionID: item.approval.fromSession, draft: updated)
            self.editing = nil; model.replyEditorState = nil; model.presentApproval(id: id)
        } catch { self.error = error.localizedDescription }
    }
    private func discard(_ item: TaskApproval) async {
        busy = true; defer { busy = false }
        let retained = model.replyEditorState
        do { model.replyEditorState = nil; try await model.freshReplyReview(item); editing = nil }
        catch { model.replyEditorState = retained; self.error = error.localizedDescription }
    }
    private func fresh(_ item: TaskApproval) async {
        busy = true; defer { busy = false }
        do { try await model.freshReplyReview(item) } catch { self.error = error.localizedDescription }
    }
    private func loadSource(_ item: TaskApproval, email: EmailReviewPresentation, reportError: Bool = true) async {
        do {
            let response = try await model.client.call(method: "email.source_list", params: ["session_id": item.approval.fromSession]) as? [String: Any]
            guard let retained = (response?["sources"] as? [[String: Any]])?.first(where: { $0["id"] as? String == email.sourceID }),
                  let body = retained["body"] as? String else { throw DaemonClientError.daemonError("Retained source message is unavailable.") }
            source = body
            sourceMetadata = "From: \(retained["from"] as? String ?? "")\nSubject: \(retained["subject"] as? String ?? "")\nCaptured: \(retained["created_at"] as? String ?? "Unknown")"
            sourceRecipient = retained["reply_to"] as? String ?? ""
        } catch { if reportError { self.error = error.localizedDescription } }
    }
    private func reconcile(_ item: TaskApproval, outcome: String) async {
        busy = true; defer { busy = false }
        do { try await model.reconcileEmailOperation(sessionID: item.approval.fromSession, operationID: item.operationID, outcome: outcome, reason: evidence); reconciling = false; await model.refreshTaskApprovals(sessionID: item.approval.fromSession) }
        catch { self.error = error.localizedDescription }
    }
}
