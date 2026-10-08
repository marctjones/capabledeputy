import SwiftUI

private struct EmailWorkspaceTarget: Identifiable {
    let id: String
}

/// Primary CapDep surface — durable chat workspace; operator console is secondary.
struct ChatView: View {
    @EnvironmentObject private var model: CapDepAppModel
    @Environment(\.openWindow) private var openWindow
    @FocusState private var inputFocused: Bool
    @State private var inspectedRequest: RequestInspectionTarget?
    @State private var inspectingSession = false
    @State private var emailWorkspace: EmailWorkspaceTarget?
    @State private var choosingSession = false
    @State private var sessionSearch = ""
    @State private var sessionDrafts: [String: String] = [:]
    @State private var followsLatest = true
    @State private var transcriptHeight: CGFloat = 0
    @State private var hasUnreadContent = false
    @State private var previousScrollTop: CGFloat?

    var body: some View {
        VStack(spacing: 0) {
            SessionPostureBar(inspect: { inspectingSession = true })
            if model.approvalWindowID == nil && !model.showingApprovalQueue {
                HStack {
                Button {
                    guard let sessionID = model.currentSessionID else { return }
                    emailWorkspace = EmailWorkspaceTarget(id: sessionID)
                } label: {
                    Label("Email Replies", systemImage: "envelope.badge")
                }.disabled(model.currentSessionID == nil)
                    .accessibilityIdentifier("capdep.email.open")
                if !model.pendingApprovals.isEmpty {
                    Button {
                        model.presentApprovalQueue()
                    } label: {
                        Label("\(model.pendingApprovals.count) need review", systemImage: "hand.raised.fill")
                    }.accessibilityIdentifier("capdep.task-review.queue")
                }

                    Spacer()
                }.padding(.horizontal, 16).padding(.vertical, 8)
            }
            Divider()
            ZStack {
                VStack(spacing: 0) {
                    conversationArea
                    Divider()
                    composerArea
                }
                .opacity(model.approvalWindowID != nil || model.showingApprovalQueue ? 0 : 1)
                .allowsHitTesting(model.approvalWindowID == nil && !model.showingApprovalQueue)
                .accessibilityHidden(model.approvalWindowID != nil || model.showingApprovalQueue)
                if model.showingApprovalQueue {
                    TaskApprovalQueueView()
                } else if let id = model.approvalWindowID {
                    TaskApprovalReviewView(approvalID: id).id(id)
                }
            }
        }
        .frame(minWidth: 440, minHeight: 560)
        .navigationTitle("CapDep")
        .accessibilityElement(children: .contain)
        .accessibilityLabel("CapDep chat workspace")
        .sheet(item: $inspectedRequest) { target in
            RequestInspectorView(target: target)
                .environmentObject(model)
        }
        .capdepBackgroundSafeSheet(isPresented: $choosingSession, title: "Conversations") {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Text("Conversations").font(.title2.weight(.semibold))
                    Spacer()
                    Button("Done") { choosingSession = false }
                }
                TextField("Search conversations", text: $sessionSearch)
                    .accessibilityIdentifier("capdep.chat.conversation-search")
                List(model.sessions.filter {
                    sessionSearch.isEmpty || "\($0.intent) \($0.id)".localizedCaseInsensitiveContains(sessionSearch)
                }.sorted { $0.updatedAt > $1.updatedAt }) { session in
                    Button {
                        model.focusSession(session)
                        choosingSession = false
                        inputFocused = true
                    } label: {
                        HStack {
                            Image(systemName: session.id == model.currentSessionID ? "checkmark.circle.fill" : "bubble.left")
                            VStack(alignment: .leading) {
                                Text(session.intent.isEmpty ? "Untitled conversation" : session.intent)
                                let waiting = model.pendingApprovals.filter { $0.fromSession == session.id }.count
                                if waiting > 0 { Text("\(waiting) need review").foregroundStyle(.orange).font(.callout) }
                                Text("\(session.id.prefix(8)) · \(session.status)").font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer()
                        }.contentShape(Rectangle())
                    }.buttonStyle(.plain)
                        .accessibilityIdentifier("capdep.chat.conversation.\(session.id)")
                }
                .accessibilityIdentifier("capdep.chat.conversations")
            }.padding(20).frame(width: 560, height: 440)
        }
        .onChange(of: model.currentSessionID) { old, new in
            sessionDrafts[old ?? "unsessioned"] = model.commandText
            model.commandText = sessionDrafts[new ?? "unsessioned"] ?? ""
            followsLatest = true
            hasUnreadContent = false
            previousScrollTop = nil
        }
        .sheet(isPresented: $inspectingSession) {
            ChatSessionInspectorView().environmentObject(model)
        }
        .capdepBackgroundSafeSheet(item: $emailWorkspace) { target in
            EmailDraftsView(sessionID: target.id).environmentObject(model)
        }
        .task(id: model.currentSessionID) {
            if let id = model.currentSessionID {
                await model.refreshSecurityContext(sessionID: id)
                await model.refreshTaskApprovals(sessionID: id)
            }
        }
        .toolbar {
            ToolbarItemGroup(placement: .primaryAction) {
                sessionMenu.disabled(model.approvalWindowID != nil)
                purposePicker.disabled(model.approvalWindowID != nil)
            }
            ToolbarItemGroup(placement: .automatic) {
                connectionBadge

                Button {
                    capdepOpenWindow(openWindow, id: "console")
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
        .onChange(of: model.grantPromptPresented) { _, presented in
            if presented {
                capdepOpenWindow(openWindow, id: "capability-grant-card")
            }
        }
        .onChange(of: model.isOnboardingPresented) { _, presented in
            if presented {
                capdepOpenWindow(openWindow, id: "onboarding-wizard")
            }
        }
        .task {
            // First-run wizard may have been requested during model.start()
            // before this view began observing.
            if model.isOnboardingPresented {
                capdepOpenWindow(openWindow, id: "onboarding-wizard")
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
                            HStack(alignment: .top) {
                                Button {
                                    inspectedRequest = RequestInspectionTarget(
                                        id: message.id, prompt: message.content,
                                        sessionID: model.currentSessionID, turnID: message.requestTurnID,
                                    )
                                } label: {
                                    Image(systemName: requestIcon(message.requestTurnID))
                                }
                                .buttonStyle(.borderless)
                                .help("Inspect request status and processing history")
                                .accessibilityLabel("Inspect request")
                                .accessibilityIdentifier("capdep.chat.request-inspector")
                                UserMessageBubble(text: message.content)
                            }
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
                        PromptQueuePanel(runs: model.promptRuns) { run in
                            inspectedRequest = RequestInspectionTarget(
                                id: run.id, prompt: run.displayMessage, sessionID: run.sessionID,
                                turnID: run.turnID, promptRunID: run.id,
                            )
                        }
                    }

                    if let sessionID = model.currentSessionID {
                        TaskApprovalCards(sessionID: sessionID)
                    }

                    if let grantStep = model.pendingGrantRecovery,
                       let sessionID = model.currentSessionID {
                        capabilityGrantBanner(step: grantStep, sessionID: sessionID)
                    }

                    if model.pendingOverrideRequired != nil {
                        overrideBanner
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
                    Color.clear.frame(height: 1)
                        .id("chat-bottom")

                }
                .padding(.horizontal, 24)
                .padding(.vertical, 20)
                .frame(maxWidth: 980, alignment: .leading)
                .frame(maxWidth: .infinity, alignment: .center)
                .background(GeometryReader { geometry in
                    let frame = geometry.frame(in: .named("chat-scroll"))
                    Color.clear.preference(key: ChatScrollPreference.self, value: ChatScrollPosition(top: frame.minY, bottom: frame.maxY))
                })
            }
            .coordinateSpace(name: "chat-scroll")
            .background(GeometryReader { geometry in
                Color.clear.preference(key: ChatViewportPreference.self, value: geometry.size.height)
            })
            .onPreferenceChange(ChatViewportPreference.self) { transcriptHeight = $0 }
            .onPreferenceChange(ChatScrollPreference.self) { position in
                followsLatest = position.shouldFollow(previousTop: previousScrollTop, viewportHeight: transcriptHeight, wasFollowing: followsLatest)
                previousScrollTop = position.top
                if followsLatest {
                    hasUnreadContent = false
                    if position.bottom > transcriptHeight + 1 {
                        proxy.scrollTo("chat-bottom", anchor: .bottom)
                    }
                }
            }
            .overlay(alignment: .bottomTrailing) {
                if !followsLatest {
                    Button(hasUnreadContent ? "New content · Jump to latest" : "Jump to latest") {
                        followsLatest = true
                        hasUnreadContent = false
                        proxy.scrollTo("chat-bottom", anchor: .bottom)
                    }
                    .buttonStyle(.borderedProminent)
                    .accessibilityIdentifier("capdep.chat.jump-latest")
                    .padding(12)
                }
            }
            .onChange(of: model.currentSessionID) { _, _ in
                proxy.scrollTo("chat-bottom", anchor: .bottom)
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
        guard followsLatest else {
            hasUnreadContent = true
            return
        }
        // Avoid per-token animations, and include statuses/approvals below the last message.
        proxy.scrollTo("chat-bottom", anchor: .bottom)
    }

    private func requestIcon(_ turnID: String?) -> String {
        guard let turnID else { return "info.circle" }
        if model.currentTurnID == turnID && model.isRunningTurn { return "arrow.triangle.2.circlepath" }
        switch model.requestTurns[turnID]?["status"] as? String {
        case "completed": return "checkmark.circle"
        case "interrupted": return "stop.circle"
        case "error": return "exclamationmark.circle"
        default: return "info.circle"
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

    private var overrideBanner: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 4) {
                Text("Override required")
                    .font(.headline)
                Text("This action crosses a hard security floor. It can only proceed through an explicit, dual-controlled override — never a normal approval.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Text("Open the override control to request it, then have a distinct principal attest.")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Button("Open override control") {
                model.presentOverrideRequest()
                capdepOpenWindow(openWindow, id: "override-card")
            }
            .buttonStyle(.borderedProminent)
        }
        .padding(14)
        .background(.purple.opacity(0.12), in: RoundedRectangle(cornerRadius: 14))
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
                capdepOpenWindow(openWindow, id: "capability-grant-card")
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
                    choosingSession = true
                } label: {
                    Label("Conversations", systemImage: "list.bubble")
                }
                .font(.caption).buttonStyle(.bordered).controlSize(.small)
                .disabled(model.isRunningTurn)
                .accessibilityIdentifier("capdep.chat.browse-conversations")

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
                .disabled(model.isRunningTurn || !model.connected)
            }
        }
        .padding(16)
        .background(.bar)
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Message composer")
    }

    private var sessionMenu: some View {
        Menu {
            Button("New chat session") {
                Task {
                    await model.createSession(intent: "New chat", purpose: model.selectedPurpose)
                }
            }
            Divider()
            ForEach(model.sessions.filter { $0.status == "active" }.sorted { $0.updatedAt > $1.updatedAt }.prefix(8)) { session in
                Button {
                    model.focusSession(session)
                } label: {
                    Label(
                        "\(session.intent.isEmpty ? "Session" : String(session.intent.prefix(60))) · \(session.id.prefix(8))",
                        systemImage: session.id == model.currentSessionID ? "checkmark.circle.fill" : "bubble.left",
                    )
                }
            }
            Button("Browse all conversations…") { choosingSession = true }
            Button("All sessions in Console…") {
                model.selectedSection = .sessions
                model.focusedSessionID = model.currentSessionID
                capdepOpenWindow(openWindow, id: "console")
            }
            if model.sessions.isEmpty {
                Text("No sessions yet")
            }
        } label: {
            Label(model.currentSessionID.map { "Chat · \($0.prefix(8))" } ?? "Choose conversation", systemImage: "bubble.left.and.bubble.right")
        }
        .disabled(model.isRunningTurn)
        .accessibilityIdentifier("capdep.chat.session-menu")
        .help(model.isRunningTurn ? "Finish or cancel the current turn before switching conversations" : "Switch or reopen a conversation")
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
            if let sessionID = model.currentSessionID,
               let summary = model.modelSummary(for: sessionID) {
                return summary
            }
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
                .accessibilityElement(children: .contain)
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
                copyIdentifier: "capdep.chat.copy-response-markdown",
            )
            .font(.system(size: 14, design: .default))
            .lineSpacing(4)
            .accessibilityElement(children: .contain)
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

/// A request-scoped read-only view of daemon facts, not planner narration.
/// A presentation of daemon facts, never a second authorization engine.
private struct SessionPostureBar: View {
    @EnvironmentObject private var model: CapDepAppModel
    let inspect: () -> Void

    var body: some View {
        let id = model.currentSessionID
        let session = model.sessions.first { $0.id == id }
        let context = id.flatMap { model.sessionSecurityContexts[$0] }
        let failed = id.flatMap { model.sessionSecurityErrors[$0] } != nil
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Label(session.map { $0.intent.isEmpty ? "Current session" : $0.intent } ?? "Current session", systemImage: "bubble.left.and.bubble.right")
                    .font(.subheadline.weight(.semibold)).lineLimit(1)
                Text(id.map { String($0.prefix(8)) } ?? "No session")
                    .font(.caption.monospaced()).foregroundStyle(.secondary)
                if let purpose = session?.purpose, !purpose.isEmpty {
                    Text("Purpose: \(purpose)").font(.caption).foregroundStyle(.secondary).lineLimit(1)
                }
                Spacer()
                Button("Session details", action: inspect)
                    .disabled(id == nil)
                    .accessibilityIdentifier("capdep.chat.session-inspector")
            }
            HStack(spacing: 12) {
                if !model.connected || failed {
                    Label("Security state unavailable / stale", systemImage: "exclamationmark.triangle")
                        .foregroundStyle(.orange)
                } else if let context, context.hasPostureSummary {
                    Label(context.hasEmailReadGrant ? "Email read grant" : "No email read grant", systemImage: "envelope")
                    if !context.sensitiveCategories.isEmpty {
                        Label("Sensitive: \(context.sensitiveCategories.joined(separator: ", "))", systemImage: "lock.fill")
                            .foregroundStyle(.orange)
                    }
                    if context.externalUntrusted {
                        Label("Untrusted content taint", systemImage: "exclamationmark.shield")
                            .foregroundStyle(.orange)
                    }
                    if context.labelCount == 0 {
                        Text("No data labels recorded").foregroundStyle(.secondary)
                    }
                    Text("\(context.availableTools.count) tools · \(context.enforcementMode)")
                        .foregroundStyle(.secondary)
                    if context.pendingApprovalCount > 0 {
                        Text("\(context.pendingApprovalCount) approvals pending").foregroundStyle(.orange)
                    }
                } else if context != nil {
                    Text("Detailed posture summary not provided by this daemon").foregroundStyle(.secondary)
                } else {
                    Text("Loading daemon security state…").foregroundStyle(.secondary)
                }
            }
            .font(.caption)
            .help("Grants are scoped, not blanket access. Labels may restrict egress or execution; inspect daemon decisions for exact restrictions.")
        }
        .padding(.horizontal, 24).padding(.vertical, 10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.quaternary.opacity(0.2))
        .accessibilityIdentifier("capdep.chat.session-summary")
    }
}

private struct ChatSessionInspectorView: View {
    @EnvironmentObject private var model: CapDepAppModel
    @Environment(\.dismiss) private var dismiss
    @State private var toolSearch = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Current session").font(.title2.weight(.semibold))
                Spacer()
                Button("Refresh") { Task { await refresh() } }
                Button("Done") { dismiss() }
            }
            if let id = model.currentSessionID {
                Text(id).font(.caption.monospaced()).textSelection(.enabled)
                if let updated = model.sessionSecurityUpdatedAt[id] {
                    Text("Daemon snapshot: \(updated.formatted(date: .omitted, time: .standard))")
                        .font(.caption).foregroundStyle(.secondary)
                }
                if !model.connected || model.sessionSecurityErrors[id] != nil {
                    Text("Disconnected or refresh failed. Cached properties may be stale; they do not authorize actions.")
                        .foregroundStyle(.orange)
                }
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        if let context = model.sessionSecurityContexts[id] {
                            DisclosureGroup("Identity, purpose and workflow configuration") {
                                ForEach(context.sessionDetails.keys.sorted(), id: \.self) { key in
                                    HStack(alignment: .top) {
                                        Text(key).foregroundStyle(.secondary)
                                        Spacer()
                                        Text(context.sessionDetails[key] ?? "").textSelection(.enabled)
                                    }.font(.caption)
                                }
                                Text("A workflow name is not inferred from conversation text. Execution preferences and flow-pattern evidence below come from the daemon.")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                            DisclosureGroup("Find tools and check planner selection") {
                                TextField("Filter registered tools", text: $toolSearch)
                                let selected = Set(context.toolSurfaceSelections.last?.selected ?? [])
                                ForEach(context.availableTools.filter {
                                    toolSearch.isEmpty || "\($0.name) \($0.capabilityKind) \($0.description)".localizedCaseInsensitiveContains(toolSearch)
                                }) { tool in
                                    VStack(alignment: .leading, spacing: 3) {
                                        Text(tool.name).font(.subheadline.weight(.medium))
                                        Text("\(tool.capabilityKind) · \(context.grantKinds.contains(tool.capabilityKind) ? "kind grant present" : "no kind grant") · \(selected.contains(tool.name) ? "selected in latest recorded surface" : "not selected in latest recorded surface")")
                                            .font(.caption).foregroundStyle(.secondary)
                                    }.frame(maxWidth: .infinity, alignment: .leading).padding(.vertical, 4)
                                }
                            }
                            DisclosureGroup("Decision context, usage and revocation") {
                                ForEach(context.decisionContextDetails.keys.sorted(), id: \.self) { key in
                                    Text("\(key): \(context.decisionContextDetails[key] ?? "")")
                                        .font(.caption.monospaced()).textSelection(.enabled)
                                }
                                ForEach(context.capabilityUsageDetails.keys.sorted(), id: \.self) { key in
                                    Text("\(key): \(context.capabilityUsageDetails[key] ?? "")")
                                        .font(.caption.monospaced()).textSelection(.enabled)
                                }
                            }
                            SecurityContextSummaryView(context: context)
                            DisclosureGroup("Saved session artifacts") {
                                SessionArtifactInspectorView(
                                    artifacts: model.sessionArtifacts[id] ?? [],
                                    labels: model.sessionArtifactLabels[id] ?? [],
                                )
                            }
                        } else {
                            Text("No daemon snapshot available.")
                        }
                    }
                }
            }
        }
        .padding(20).frame(minWidth: 720, minHeight: 580)
        .task(id: model.currentSessionID) { await refresh() }
    }

    private func refresh() async {
        if let id = model.currentSessionID {
            await model.refreshSecurityContext(sessionID: id)
            await model.loadSessionArtifacts(sessionID: id)
        }
    }
}

private struct RequestInspectorView: View {
    @EnvironmentObject private var model: CapDepAppModel
    @Environment(\.dismiss) private var dismiss
    @Environment(\.openWindow) private var openWindow
    let target: RequestInspectionTarget
    @State private var snapshot: [String: Any] = [:]
    @State private var events: [RequestTraceEvent] = []
    @State private var security: [String: Any] = [:]
    @State private var cursor = 0
    @State private var hasMore = false
    @State private var error: String?
    @State private var showHeartbeats = false
    @State private var showModelTrace = false

    private var turnID: String? {
        target.turnID ?? model.chatMessages.first(where: { $0.id == target.id })?.requestTurnID
            ?? model.promptRuns.first(where: { $0.id == target.promptRunID })?.turnID
    }

    private var visibleEvents: [RequestTraceEvent] {
        events.filter { (showHeartbeats || !$0.isNoise) && (showModelTrace || !$0.isModelTrace) }
    }

    private var elapsed: String {
        let parser = ISO8601DateFormatter()
        parser.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        guard let raw = snapshot["created_at"] as? String, let start = parser.date(from: raw) else { return "" }
        let status = snapshot["status"] as? String ?? ""
        let end = ["completed", "interrupted", "error"].contains(status)
            ? parser.date(from: snapshot["updated_at"] as? String ?? "") ?? Date() : Date()
        return String(format: "%.1f seconds", max(0, end.timeIntervalSince(start)))
    }

    private var phase: String {
        if let event = events.last(where: { !$0.isNoise }) { return event.title }
        return "Waiting for daemon events"
    }

    private var pendingApprovalIDs: [Int] {
        let ids = events.compactMap { event -> Int? in
            let outcome = event.payload["outcome"] as? [String: Any] ?? [:]
            return outcome["approval_id"] as? Int
        }
        return Array(Set(ids)).filter { id in model.pendingApprovals.contains { $0.id == id } }.sorted()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Request details").font(.title2.bold())
                Spacer()
                Button("Done") { dismiss() }.keyboardShortcut(.cancelAction)
            }
            Text(target.prompt).lineLimit(3).textSelection(.enabled)
            if turnID == nil {
                Text("No daemon request ID is available yet. Queued prompts update here when started; older chat messages may predate durable request history.")
                    .foregroundStyle(.secondary)
            } else {
                let status = snapshot["status"] as? String ?? "Loading"
                HStack {
                    Text("Status: \(status)").font(.headline)
                    Spacer()
                    Text(elapsed).font(.caption.monospacedDigit())
                    if let date = snapshot["created_at"] as? String {
                        Text("Started: \(date)").font(.caption).textSelection(.enabled)
                    }
                }
                Text(phase).font(.subheadline).foregroundStyle(.secondary)
                if !pendingApprovalIDs.isEmpty {
                    Text("Awaiting human approval").foregroundStyle(.orange)
                    ForEach(pendingApprovalIDs, id: \.self) { id in
                        Button("Review requested action") {
                            model.presentApproval(id: id)
                            capdepOpenWindow(openWindow, id: "main")
                        }
                    }
                }
                if model.currentTurnID == turnID && model.isRunningTurn {
                    Button("Cancel this request") { Task { await model.cancelCurrentTurn() } }
                }
                if let heartbeat = snapshot["last_heartbeat_at"] as? String {
                    Text("Last daemon heartbeat: \(heartbeat) (liveness, not proof of progress)")
                        .font(.caption2).foregroundStyle(.secondary)
                }
                if snapshot["history_incomplete"] as? Bool == true {
                    Text("History is incomplete: the daemon stopped during this request. It has not been resumed.")
                        .foregroundStyle(.orange)
                }
                if let reason = snapshot["cancel_reason"] as? String { Text(reason).font(.caption) }
                if let failure = snapshot["error"] as? String { Text(failure).foregroundStyle(.red) }
                HStack {
                    Toggle("Heartbeat / token events", isOn: $showHeartbeats)
                    Toggle("Model-emitted reasoning traces", isOn: $showModelTrace)
                }.font(.caption)
                Text("Daemon events describe execution and policy. Model traces, if emitted, are untrusted model output—not authorization or reconstructed hidden reasoning.")
                    .font(.caption).foregroundStyle(.secondary)
                if showModelTrace && !events.contains(where: { $0.isModelTrace }) {
                    Text("No model-emitted reasoning trace has been recorded in the loaded events.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 10) {
                        ForEach(visibleEvents) { event in
                            DisclosureGroup {
                                Text(RequestTraceEvent.redactedJSON(event.payload))
                                    .font(.caption.monospaced()).textSelection(.enabled)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                            } label: {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(event.title).font(.subheadline.weight(.medium))
                                    Text("#\(event.cursor) · \(event.timestamp)").font(.caption2).foregroundStyle(.secondary)
                                }
                            }
                        }
                        if hasMore {
                            Button("Load more history") { Task { await loadEvents() } }
                        }
                        DisclosureGroup("Current session security context (not a historical snapshot)") {
                            Text(RequestTraceEvent.redactedJSON(security))
                                .font(.caption.monospaced()).textSelection(.enabled)
                        }
                    }.padding(4)
                }
                .accessibilityIdentifier("capdep.request.timeline")
            }
            if let error { Text(error).foregroundStyle(.red).font(.caption) }
            Text("Read-only inspection • Credential fields are redacted • No authority is granted here")
                .font(.caption2).foregroundStyle(.secondary)
        }
        .padding(20).frame(minWidth: 660, idealWidth: 760, minHeight: 500, idealHeight: 640)
        .accessibilityIdentifier("capdep.request.inspector")
        .task(id: turnID) {
            guard let turnID else { return }
            events = []; cursor = 0
            security = [:]
            while !Task.isCancelled {
                do {
                    snapshot = try await model.inspectRequest(turnID: turnID)
                    if security.isEmpty, let sessionID = snapshot["session_id"] as? String {
                        security = (try? await model.client.call(method: "session.security_context", params: ["session_id": sessionID])) as? [String: Any] ?? [:]
                    }
                    await loadEvents()
                    let status = snapshot["status"] as? String ?? ""
                    if ["completed", "interrupted", "error"].contains(status) && !hasMore { return }
                } catch {
                    self.error = "Could not read request history: \(error.localizedDescription)"
                }
                do { try await Task.sleep(for: .seconds(1)) } catch { return }
            }
        }
    }

    private func loadEvents() async {
        guard let turnID else { return }
        do {
            let response = try await model.client.call(method: "session.turn.events", params: ["turn_id": turnID, "cursor": cursor, "limit": 200]) as? [String: Any] ?? [:]
            let incoming = (response["events"] as? [[String: Any]] ?? []).map(RequestTraceEvent.init(dictionary:))
            let known = Set(events.map(\.cursor))
            events.append(contentsOf: incoming.filter { !known.contains($0.cursor) })
            cursor = response["next_cursor"] as? Int ?? cursor
            hasMore = response["has_more"] as? Bool ?? false
        } catch { self.error = error.localizedDescription }
    }
}

private struct PromptQueuePanel: View {
    let runs: [ChatPromptRun]
    var inspect: (ChatPromptRun) -> Void

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
                    Button { inspect(run) } label: { Image(systemName: icon(for: run.status)) }
                        .buttonStyle(.borderless)
                        .accessibilityLabel("Inspect request")
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
        case .interrupted: "stop.circle"
        case .failed: "exclamationmark.triangle"
        }
    }

    private func color(for status: ChatPromptStatus) -> Color {
        switch status {
        case .queued: .secondary
        case .running: .blue
        case .completed: .green
        case .interrupted: .orange
        case .failed: .red
        }
    }
}

private struct ChatScrollPreference: PreferenceKey {
    static let defaultValue = ChatScrollPosition(top: 0, bottom: 0)
    static func reduce(value: inout ChatScrollPosition, nextValue: () -> ChatScrollPosition) { value = nextValue() }
}
private struct ChatViewportPreference: PreferenceKey {
    static let defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) { value = nextValue() }
}
