import SwiftUI

/// Primary CapDep surface — durable chat workspace; operator console is secondary.
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
        .accessibilityIdentifier("capdep.chat.window")
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
        .onChange(of: model.approvalWindowID) { _, approvalID in
            if approvalID != nil {
                openWindow(id: "approval-card")
            }
        }
        .onChange(of: model.grantPromptPresented) { _, presented in
            if presented {
                openWindow(id: "capability-grant-card")
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
                                authorizedImagePaths: model.authorizedStreamingImagePaths,
                                holdUnverifiedGeneratedImages: message.isStreaming && model.isRunningTurn,
                                onContentSizeChange: {
                                    scrollToLatest(proxy: proxy)
                                },
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
                        .accessibilityIdentifier("capdep.chat.turn-progress")
                        .id("turn-progress")
                    }

                    if !model.promptRuns.isEmpty {
                        PromptQueuePanel(runs: model.promptRuns)
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
                .padding(.horizontal, 24)
                .padding(.vertical, 20)
                .frame(maxWidth: 980, alignment: .leading)
                .frame(maxWidth: .infinity, alignment: .center)
            }
            .onChange(of: model.chatMessages.count) { _, _ in
                scrollToLatest(proxy: proxy)
            }
            .onChange(of: model.chatScrollAnchor) { _, _ in
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
            Text("Ask about the current app, mail, calendar, files, or research. CapDep will pause when it needs your approval.")
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
                Text("CapDep paused before a sensitive action. Review what will happen, then approve or deny.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                if !model.turnPendingApprovalIDs.isEmpty {
                    Text(
                        "Pending: "
                            + model.turnPendingApprovalIDs.map { "#\($0)" }.joined(separator: ", "),
                    )
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                }
            }
            Spacer()
            Button("Review approval") {
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
        let grantPattern = step.guiGrantPattern() ?? step.grantPattern
        let canRetry = model.pendingGrantRetryMessage != nil
        let title = step.isWebSearchGrant ? "Allow web search?" : "Allow access to this location?"
        let actionLabel = step.isWebSearchGrant ? "Open web search prompt" : (canRetry ? "Open access prompt" : "Review access request")
        return HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 4) {
                Text(title)
                    .font(.headline)
                if let kind = step.grantKind, let grantPattern {
                    Text("CapDep needs \(kind) permission for \(grantPattern) before it can continue.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                if !step.rationale.isEmpty {
                    Text(step.rationale)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
                Text(
                    canRetry
                        ? "This is a capability grant, not an approval. Tap below to allow access and retry your request."
                        : "This is a capability grant, not an approval. Grant access, then ask again.",
                )
                .font(.caption2)
                .foregroundStyle(.secondary)
            }
            Spacer()
            Button(actionLabel) {
                model.grantPromptPresented = true
                openWindow(id: "capability-grant-card")
            }
            .buttonStyle(.borderedProminent)
        }
        .padding(14)
        .background(.yellow.opacity(0.12), in: RoundedRectangle(cornerRadius: 14))
    }

    private var composerArea: some View {
        VStack(alignment: .leading, spacing: 9) {
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
                .accessibilityIdentifier("capdep.chat.input")
                .accessibilityLabel("CapDep chat input")
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
                        model.commandText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                    )
                    .accessibilityIdentifier("capdep.chat.send")
                    .accessibilityLabel("Send CapDep message")

                    if model.isRunningTurn {
                        Button {
                            Task { await model.cancelCurrentTurn() }
                        } label: {
                            Image(systemName: "stop.circle")
                                .foregroundStyle(.red)
                        }
                        .buttonStyle(.plain)
                        .accessibilityIdentifier("capdep.chat.cancel-turn")
                        .accessibilityLabel("Cancel CapDep turn")
                    }
                }
            }

            HStack(spacing: 10) {
                Button {
                    Task { await model.refreshFrontmostContext() }
                } label: {
                    Label(model.contextChips.isEmpty ? "Use Current App" : "Refresh Context", systemImage: "scope")
                }
                .font(.caption)
                .buttonStyle(.bordered)
                .controlSize(.small)
                .accessibilityIdentifier("capdep.chat.refresh-context")

                Text(modelModeHelperText)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)

                Spacer(minLength: 12)

                Menu {
                    Picker("Model", selection: $model.selectedChatModelMode) {
                        ForEach(ChatModelMode.allCases) { mode in
                            Label(mode.title, systemImage: mode.systemImage)
                                .tag(mode)
                        }
                    }
                    Divider()
                    Text(model.selectedChatModelMode.helpText)
                    if let sessionID = model.currentSessionID {
                        Divider()
                        Text("Session \(sessionID.prefix(8))")
                    }
                } label: {
                    Label(model.selectedChatModelMode.title, systemImage: model.selectedChatModelMode.systemImage)
                }
                .font(.caption)
                .menuStyle(.button)
                .controlSize(.small)
                .accessibilityIdentifier("capdep.chat.model-mode")

                Button {
                    Task {
                        await model.createSession(
                            intent: "New chat",
                            purpose: model.selectedPurpose,
                        )
                        inputFocused = true
                    }
                } label: {
                    Label("New Session", systemImage: "plus.bubble")
                }
                .font(.caption)
                .buttonStyle(.bordered)
                .controlSize(.small)
                .accessibilityIdentifier("capdep.chat.new-session")
            }
        }
        .padding(16)
        .background(.bar)
        .accessibilityIdentifier("capdep.chat.composer")
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
            model.daemonConnection.statusTitle,
            systemImage: model.daemonConnection.phase.systemImage,
        )
        .font(.caption)
        .foregroundStyle(model.connected ? .green : .red)
        .accessibilityIdentifier("capdep.chat.connection-status")
        .accessibilityLabel("CapDep daemon connection \(model.daemonConnection.statusTitle)")
    }

    private var shouldShowWelcome: Bool {
        model.chatMessages.isEmpty && !model.isRunningTurn
    }

    private var modelModeHelperText: String {
        guard model.isRunningTurn, !model.currentResolvedModelRole.isEmpty else {
            return model.contextChips.isEmpty ? model.selectedChatModelMode.helpText : "\(model.contextChips.count) context item\(model.contextChips.count == 1 ? "" : "s") attached"
        }
        let title: String
        switch model.currentResolvedModelRole {
        case "planner.fast":
            title = "Fast"
        case "planner.tools":
            title = "Tools"
        case "planner.quality":
            title = "Quality"
        default:
            title = model.currentResolvedModelRole
        }
        let reason = model.currentResolvedModelReason
        return reason.isEmpty ? "Using \(title)" : "Using \(title) (\(reason))"
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
            ChatRichMessageBody(text: text)
                .font(.system(size: 14, design: .default))
                .lineSpacing(3)
                .padding(.horizontal, 13)
                .padding(.vertical, 9)
                .frame(maxWidth: 680, alignment: .leading)
                .background(.quaternary.opacity(0.42), in: RoundedRectangle(cornerRadius: 8))
                .accessibilityIdentifier("capdep.chat.message.user")
        }
    }
}

private struct AssistantMessageBubble: View {
    let text: String
    var isStreaming: Bool = false
    var authorizedImagePaths: Set<String> = []
    var holdUnverifiedGeneratedImages: Bool = false
    var onContentSizeChange: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                Image(systemName: "shield")
                    .font(.caption)
                Text("CapDep")
                    .font(.caption.weight(.semibold))
            }
            .foregroundStyle(.secondary)
            ChatRichMessageBody(
                text: text,
                isStreaming: isStreaming,
                authorizedImagePaths: authorizedImagePaths,
                holdUnverifiedGeneratedImages: holdUnverifiedGeneratedImages,
                onContentSizeChange: onContentSizeChange,
            )
            .font(.system(size: 14, design: .default))
            .lineSpacing(4)
            .accessibilityIdentifier("capdep.chat.message.assistant")
        }
        .padding(.vertical, 2)
        .frame(maxWidth: 760, alignment: .leading)
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

private struct PromptQueuePanel: View {
    let runs: [ChatPromptRun]

    private var visibleRuns: [ChatPromptRun] {
        Array(runs.suffix(8))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Prompt activity")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
            ForEach(visibleRuns) { run in
                HStack(alignment: .top, spacing: 8) {
                    Image(systemName: icon(for: run.status))
                        .foregroundStyle(color(for: run.status))
                        .font(.caption)
                        .frame(width: 14)
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: 6) {
                            Text(run.status.title)
                                .font(.caption.weight(.semibold))
                            if let turnID = run.turnID, !turnID.isEmpty {
                                Text(String(turnID.prefix(8)))
                                    .font(.caption2.monospaced())
                                    .foregroundStyle(.secondary)
                            }
                        }
                        Text(run.displayMessage)
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                            .lineLimit(2)
                        if let error = run.error, !error.isEmpty {
                            Text(error)
                                .font(.caption2)
                                .foregroundStyle(.red)
                                .lineLimit(2)
                        }
                    }
                    Spacer(minLength: 0)
                }
            }
        }
        .padding(12)
        .background(.quaternary.opacity(0.18), in: RoundedRectangle(cornerRadius: 8))
        .accessibilityIdentifier("capdep.chat.prompt-activity")
    }

    private func icon(for status: ChatPromptStatus) -> String {
        switch status {
        case .queued: "clock"
        case .running: "arrow.triangle.2.circlepath"
        case .completed: "checkmark.circle"
        case .failed: "exclamationmark.triangle"
        }
    }

    private func color(for status: ChatPromptStatus) -> Color {
        switch status {
        case .queued: .secondary
        case .running: .blue
        case .completed: .green
        case .failed: .red
        }
    }
}
