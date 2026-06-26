import SwiftUI

/// Primary CapDep surface — conversational assistant first; operator console is secondary.
struct ChatView: View {
    @EnvironmentObject private var model: CapDepAppModel
    @Environment(\.openWindow) private var openWindow
    @FocusState private var inputFocused: Bool

    var body: some View {
        VStack(spacing: 0) {
            conversationArea
            Divider()
            composerArea
        }
        .frame(minWidth: 720, minHeight: 560)
        .navigationTitle("CapDep")
        .toolbar {
            ToolbarItemGroup(placement: .primaryAction) {
                sessionMenu
                purposePicker
            }
            ToolbarItemGroup(placement: .automatic) {
                connectionBadge
                if !model.pendingApprovals.isEmpty {
                    Button {
                        if let first = model.pendingApprovals.first {
                            model.presentApproval(id: first.id)
                            openWindow(id: "approval-card")
                        }
                    } label: {
                        Label(
                            "\(model.pendingApprovals.count) Pending",
                            systemImage: "hand.raised.fill",
                        )
                    }
                }
                Button {
                    openWindow(id: "console")
                } label: {
                    Label("Console", systemImage: "rectangle.3.group")
                }
                .help("Sessions, setup, policy trace, and advanced operator tools")
            }
        }
        .onAppear {
            inputFocused = true
            if let sessionID = model.currentSessionID {
                Task {
                    await model.loadChatHistory(sessionID: sessionID)
                }
            }
        }
        .onChange(of: model.isGoogleOAuthWizardPresented) { _, presented in
            if presented {
                openWindow(id: "google-oauth-wizard")
            }
        }
    }

    private var conversationArea: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    if shouldShowWelcome {
                        welcomeHeader
                        workflowSuggestions
                    }

                    ForEach(model.chatMessages) { message in
                        switch message.role {
                        case .user:
                            UserMessageBubble(text: message.content)
                                .id(message.id)
                        case .assistant:
                            AssistantMessageBubble(
                                text: message.content,
                                isStreaming: message.isStreaming,
                            )
                            .id(message.id)
                        }
                    }

                    if model.isRunningTurn {
                        HStack(spacing: 10) {
                            ProgressView()
                                .controlSize(.small)
                            Text(model.turnStatusLine.isEmpty ? "Working…" : model.turnStatusLine)
                                .font(.subheadline)
                                .foregroundStyle(.secondary)
                        }
                        .padding(.horizontal, 4)
                        .id("turn-progress")
                    }

                    if !model.turnPendingApprovalIDs.isEmpty {
                        approvalBanner
                    }

                    if let grantStep = model.pendingGrantRecovery,
                       let sessionID = model.currentSessionID {
                        capabilityGrantBanner(step: grantStep, sessionID: sessionID)
                    }

                    if !model.currentToolOutcomes.isEmpty {
                        ToolOutcomesPanel(outcomes: model.currentToolOutcomes)
                    }

                    if let error = model.lastError, !error.isEmpty {
                        Text(error)
                            .font(.caption)
                            .foregroundStyle(.red)
                            .padding(12)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(.red.opacity(0.08), in: RoundedRectangle(cornerRadius: 12))
                    }
                }
                .padding(20)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .onChange(of: model.chatMessages.count) { _, _ in
                scrollToLatest(proxy: proxy)
            }
            .onChange(of: model.isRunningTurn) { _, running in
                if running {
                    scrollToLatest(proxy: proxy)
                }
            }
        }
    }

    private func scrollToLatest(proxy: ScrollViewProxy) {
        guard let lastID = model.chatMessages.last?.id else {
            return
        }
        withAnimation(.easeOut(duration: 0.2)) {
            proxy.scrollTo(lastID, anchor: .bottom)
        }
    }

    private var welcomeHeader: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("What can I help with?")
                .font(.title.weight(.semibold))
            Text("Ask about mail, calendar, files, or research. CapDep runs every action through the policy gate.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var workflowSuggestions: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Workflows")
                .font(.headline)
                .foregroundStyle(.secondary)
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 200), spacing: 10)], spacing: 10) {
                ForEach(model.workflows.prefix(6)) { workflow in
                    Button {
                        Task {
                            await model.launchWorkflow(workflow)
                        }
                    } label: {
                        WorkflowTile(workflow: workflow)
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }

    private var approvalBanner: some View {
        HStack {
            VStack(alignment: .leading, spacing: 4) {
                Text("Approval required")
                    .font(.headline)
                Text(
                    "Turn paused on: "
                        + model.turnPendingApprovalIDs.map { "#\($0)" }.joined(separator: ", "),
                )
                .font(.caption)
                .foregroundStyle(.secondary)
            }
            Spacer()
            Button("Review") {
                if let first = model.turnPendingApprovalIDs.first {
                    model.presentApproval(id: first)
                    openWindow(id: "approval-card")
                }
            }
            .buttonStyle(.borderedProminent)
        }
        .padding(14)
        .background(.orange.opacity(0.12), in: RoundedRectangle(cornerRadius: 14))
    }

    private func capabilityGrantBanner(step: RecoveryStep, sessionID: String) -> some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 4) {
                Text("Capability needed")
                    .font(.headline)
                if let kind = step.grantKind, let pattern = step.grantPattern {
                    Text("Grant \(kind) access to \(pattern) for this session.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                if !step.rationale.isEmpty {
                    Text(step.rationale)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
                Text("This is not an approval queue item — CapDep needs a capability grant before it can read files.")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Button("Grant access") {
                Task {
                    await model.grantCapability(from: step, sessionID: sessionID)
                }
            }
            .buttonStyle(.borderedProminent)
        }
        .padding(14)
        .background(.yellow.opacity(0.12), in: RoundedRectangle(cornerRadius: 14))
    }

    private var composerArea: some View {
        VStack(alignment: .leading, spacing: 10) {
            if !model.contextChips.isEmpty {
                ContextChipRow(chips: model.contextChips) { chip in
                    model.removeContextChip(chip)
                }
            }

            HStack(alignment: .bottom, spacing: 12) {
                TextField(
                    "Message CapDep…",
                    text: $model.commandText,
                    axis: .vertical,
                )
                .textFieldStyle(.plain)
                .font(.body)
                .lineLimit(2...6)
                .padding(12)
                .background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 14))
                .focused($inputFocused)
                .onSubmit {
                    Task { await submitMessage() }
                }

                VStack(spacing: 8) {
                    Button {
                        Task { await submitMessage() }
                    } label: {
                        Image(systemName: "arrow.up.circle.fill")
                            .font(.title2)
                    }
                    .buttonStyle(.plain)
                    .disabled(
                        model.commandText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                            || model.isRunningTurn,
                    )

                    if model.isRunningTurn {
                        Button {
                            Task { await model.cancelCurrentTurn() }
                        } label: {
                            Image(systemName: "stop.circle")
                                .foregroundStyle(.red)
                        }
                        .buttonStyle(.plain)
                    }
                }
            }

            HStack(spacing: 12) {
                if let sessionID = model.currentSessionID {
                    Text("Session \(sessionID.prefix(8))")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button("Attach frontmost app") {
                    Task { await model.refreshFrontmostContext() }
                }
                .font(.caption)
                .buttonStyle(.link)
                Button("New session") {
                    Task {
                        await model.createSession(
                            intent: "New chat",
                            purpose: model.selectedPurpose,
                        )
                        inputFocused = true
                    }
                }
                .font(.caption)
                .buttonStyle(.link)
            }
        }
        .padding(16)
        .background(.bar)
    }

    private var sessionMenu: some View {
        Menu {
            Button("New chat session") {
                Task {
                    await model.createSession(intent: "New chat", purpose: model.selectedPurpose)
                }
            }
            Divider()
            ForEach(model.sessions.filter { $0.status == "active" }.prefix(8)) { session in
                Button(session.intent.isEmpty ? "Session \(session.id.prefix(8))" : session.intent) {
                    model.focusSession(session)
                }
            }
            if model.sessions.isEmpty {
                Text("No sessions yet")
            }
        } label: {
            Label("Session", systemImage: "bubble.left.and.bubble.right")
        }
    }

    private var purposePicker: some View {
        Picker("Purpose", selection: $model.selectedPurpose) {
            ForEach(Purpose.allCases) { purpose in
                Text(purpose.rawValue.capitalized).tag(purpose)
            }
        }
        .pickerStyle(.menu)
        .frame(maxWidth: 130)
    }

    private var connectionBadge: some View {
        Label(
            model.connected ? "Connected" : "Offline",
            systemImage: model.connected ? "checkmark.circle.fill" : "xmark.octagon.fill",
        )
        .font(.caption)
        .foregroundStyle(model.connected ? .green : .red)
    }

    private var shouldShowWelcome: Bool {
        model.chatMessages.isEmpty && !model.isRunningTurn
    }

    private func submitMessage() async {
        await model.submitCommand()
        inputFocused = true
    }
}

private struct UserMessageBubble: View {
    let text: String

    var body: some View {
        HStack {
            Spacer(minLength: 48)
            Text(text)
                .font(.body)
                .textSelection(.enabled)
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .background(.quaternary.opacity(0.55), in: RoundedRectangle(cornerRadius: 16))
        }
    }
}

private struct AssistantMessageBubble: View {
    let text: String
    var isStreaming: Bool = false

    private var rendered: AttributedString {
        ChatContentFormatter.attributedMarkdown(from: text)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("CapDep")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
            if isStreaming, text.isEmpty {
                Text("…")
                    .font(.body)
                    .foregroundStyle(.secondary)
            } else {
                Text(rendered)
                    .font(.body)
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .tint(.accentColor)
            }
        }
        .padding(14)
        .background(.blue.opacity(0.08), in: RoundedRectangle(cornerRadius: 16))
    }
}

private struct ToolOutcomesPanel: View {
    let outcomes: [ToolOutcome]

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Tool activity")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
            ForEach(outcomes.prefix(6)) { outcome in
                HStack(alignment: .top, spacing: 8) {
                    Image(systemName: outcome.decision.lowercased().contains("deny") ? "xmark.octagon" : "wrench")
                        .foregroundStyle(.secondary)
                        .font(.caption)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(outcome.toolName.isEmpty ? outcome.decision : "\(outcome.toolName)")
                            .font(.caption.weight(.semibold))
                        if !outcome.reason.isEmpty || !outcome.error.isEmpty {
                            Text(outcome.error.isEmpty ? outcome.reason : outcome.error)
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                                .lineLimit(3)
                        }
                        if let step = outcome.grantRecoveryStep,
                           let kind = step.grantKind,
                           let pattern = step.grantPattern {
                            Text("Needs grant: \(kind) \(pattern)")
                                .font(.caption2)
                                .foregroundStyle(.orange)
                        }
                    }
                }
            }
        }
        .padding(12)
        .background(.quaternary.opacity(0.25), in: RoundedRectangle(cornerRadius: 12))
    }
}