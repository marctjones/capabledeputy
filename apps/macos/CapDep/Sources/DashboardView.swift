import SwiftUI

struct DashboardView: View {
    @EnvironmentObject private var model: CapDepAppModel
    @State private var selectedApproval: Approval?
    @State private var selectedSession: CapDepSession?

    var body: some View {
        NavigationSplitView {
            List(selection: $model.selectedSection) {
                ForEach(DashboardSection.allCases) { section in
                    Label(section.rawValue, systemImage: section.systemImage)
                        .tag(section)
                }
            }
            .navigationTitle("CapDep")
            .safeAreaInset(edge: .bottom) {
                StatusFooter()
            }
        } content: {
            SectionContentView(
                selectedApproval: $selectedApproval,
                selectedSession: $selectedSession,
            )
            .navigationTitle(model.selectedSection.rawValue)
        } detail: {
            InspectorView(
                selectedApproval: selectedApproval,
                selectedSession: selectedSession,
            )
        }
        .toolbar {
            ToolbarItemGroup {
                Label(model.connected ? "Connected" : "Offline", systemImage: model.connected ? "checkmark.circle" : "xmark.octagon")
                    .foregroundStyle(model.connected ? .green : .red)
                Picker("Purpose", selection: $model.selectedPurpose) {
                    ForEach(Purpose.allCases) { purpose in
                        Text(purpose.rawValue.capitalized).tag(purpose)
                    }
                }
                .pickerStyle(.menu)
                Button {
                    Task {
                        await model.refresh()
                    }
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }
                .disabled(model.isRefreshing)
            }
        }
        .onChange(of: model.focusedApprovalID) { _, id in
            guard let id else {
                return
            }
            selectedApproval = model.pendingApprovals.first { $0.id == id }
        }
        .onChange(of: model.focusedSessionID) { _, id in
            guard let id else {
                return
            }
            selectedSession = model.sessions.first { $0.id == id }
            Task {
                await model.refreshSecurityContext(sessionID: id)
            }
        }
    }
}

private struct SectionContentView: View {
    @EnvironmentObject private var model: CapDepAppModel
    @Binding var selectedApproval: Approval?
    @Binding var selectedSession: CapDepSession?

    var body: some View {
        switch model.selectedSection {
        case .today:
            TodayView()
        case .approvals:
            ApprovalQueueView(selectedApproval: $selectedApproval)
        case .sessions:
            SessionListView(selectedSession: $selectedSession)
        case .workflows:
            WorkflowLibraryView()
        case .onguard:
            OnguardView()
        case .policyTrace:
            PolicyTraceView()
        case .provenance:
            ProvenanceView()
        case .trust:
            TrustView()
        case .setup:
            SetupAssistantView()
        case .settings:
            CapDepSettingsView()
        }
    }
}

private struct TodayView: View {
    @EnvironmentObject private var model: CapDepAppModel
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                HeaderCard()
                HStack(alignment: .top, spacing: 14) {
                    MetricCard(title: "Pending Approvals", value: "\(model.pendingApprovals.count)", systemImage: "hand.raised")
                    MetricCard(title: "Sessions", value: "\(model.sessions.count)", systemImage: "rectangle.connected.to.line.below")
                    MetricCard(title: "Recent Events", value: "\(model.events.count)", systemImage: "waveform.path.ecg")
                }

                VStack(alignment: .leading, spacing: 12) {
                    HStack {
                        Text("Start Work")
                            .font(.title2.weight(.semibold))
                        Spacer()
                        Button("Ask Anything") {
                            openWindow(id: "command-palette")
                        }
                    }
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 220), spacing: 12)], spacing: 12) {
                        ForEach(model.workflows) { workflow in
                            Button {
                                Task {
                                    await model.launchWorkflow(workflow)
                                    openWindow(id: "task-panel")
                                }
                            } label: {
                                WorkflowTile(workflow: workflow)
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }

                RecentEventsView(limit: 10)
            }
            .padding(24)
        }
    }
}

private struct ApprovalQueueView: View {
    @EnvironmentObject private var model: CapDepAppModel
    @Binding var selectedApproval: Approval?

    var body: some View {
        List(selection: $selectedApproval) {
            if model.pendingApprovals.isEmpty {
                ContentUnavailableView(
                    "No Pending Approvals",
                    systemImage: "checkmark.shield",
                    description: Text("CapDep will ask when a proposed action crosses a policy boundary."),
                )
            }
            ForEach(model.pendingApprovals) { approval in
                ApprovalRow(approval: approval)
                    .tag(approval as Approval?)
            }
        }
        .overlay {
            if let approval = selectedApproval {
                ApprovalDetailView(approval: approval)
                    .background(.background)
            }
        }
    }
}

private struct ApprovalRow: View {
    let approval: Approval

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text("#\(approval.id) \(approval.action)")
                    .font(.headline)
                Spacer()
                Text(approval.status)
                    .font(.caption.weight(.semibold))
                    .padding(.horizontal, 8)
                    .padding(.vertical, 4)
                    .background(.yellow.opacity(0.18), in: Capsule())
            }
            Text(approval.target.isEmpty ? "(no target)" : approval.target)
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .lineLimit(1)
            Text(approval.justification.isEmpty ? "Review exact payload before approving." : approval.justification)
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(2)
        }
        .padding(.vertical, 6)
    }
}

private struct SessionListView: View {
    @EnvironmentObject private var model: CapDepAppModel
    @Binding var selectedSession: CapDepSession?

    var body: some View {
        List(selection: $selectedSession) {
            ForEach(model.sessions) { session in
                VStack(alignment: .leading, spacing: 6) {
                    Text(session.intent.isEmpty ? "Session \(session.id.prefix(8))" : session.intent)
                        .font(.headline)
                    Text("\(session.status) · \(session.purpose.isEmpty ? "unset" : session.purpose)")
                        .foregroundStyle(.secondary)
                    if !session.labels.isEmpty {
                        Text(session.labels.prefix(4).joined(separator: " · "))
                            .font(.caption.monospaced())
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                    }
                }
                .tag(session as CapDepSession?)
            }
        }
    }
}

private struct WorkflowLibraryView: View {
    @EnvironmentObject private var model: CapDepAppModel
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        ScrollView {
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 260), spacing: 14)], spacing: 14) {
                ForEach(model.workflows) { workflow in
                    Button {
                        Task {
                            await model.launchWorkflow(workflow)
                            openWindow(id: "task-panel")
                        }
                    } label: {
                        WorkflowTile(workflow: workflow)
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(24)
        }
    }
}

private struct PolicyTraceView: View {
    @EnvironmentObject private var model: CapDepAppModel

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                Text("Recent Policy Decisions")
                    .font(.title2.weight(.semibold))
                ForEach(model.events.filter { $0.eventType.contains("policy") }.prefix(20)) { event in
                    EventCard(event: event)
                }
                if !model.events.contains(where: { $0.eventType.contains("policy") }) {
                    PlaceholderDetailView(
                        title: "No Recent Policy Decisions",
                        systemImage: "shield",
                        message: "Policy decisions will appear here as the daemon emits audit events.",
                    )
                }
            }
            .padding(24)
        }
    }
}

private struct OnguardView: View {
    @EnvironmentObject private var model: CapDepAppModel

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("Onguard Coordination")
                    .font(.largeTitle.weight(.bold))
                Text("Read-only daemon-owned background-client state. Use this to review scheduled and queued work without giving headless clients extra privilege.")
                    .foregroundStyle(.secondary)

                HStack(alignment: .top, spacing: 14) {
                    MetricCard(title: "Clients", value: "\(model.onguardClients.count)", systemImage: "person.crop.circle.badge.clock")
                    MetricCard(title: "Queue", value: "\(model.onguardCommands.count)", systemImage: "tray.full")
                    MetricCard(title: "Schedules", value: "\(model.onguardSchedules.count)", systemImage: "calendar.badge.clock")
                }

                LiveCard(title: "Registered Clients", systemImage: "person.3.sequence") {
                    OnguardRows(
                        empty: "No onguard clients registered.",
                        rows: model.onguardClients.map {
                            ($0.id, "\($0.kind) · \($0.status)" + ($0.owner.isEmpty ? "" : " · owner \($0.owner)"))
                        },
                    )
                }

                LiveCard(title: "Queued Commands", systemImage: "list.bullet.clipboard") {
                    OnguardRows(
                        empty: "No queued commands.",
                        rows: model.onguardCommands.map {
                            ($0.command, "\($0.clientID) · \($0.status) · \($0.labels.joined(separator: ", "))")
                        },
                    )
                }

                HStack(alignment: .top, spacing: 14) {
                    LiveCard(title: "Schedules", systemImage: "calendar") {
                        OnguardRows(
                            empty: "No schedules configured.",
                            rows: model.onguardSchedules.map {
                                ($0.id, "\($0.clientID) · \($0.command) · \($0.status)" + ($0.nextRunAt.isEmpty ? "" : " · next \($0.nextRunAt)"))
                            },
                        )
                    }
                    LiveCard(title: "Artifacts", systemImage: "doc.badge.clock") {
                        OnguardRows(
                            empty: "No artifacts produced.",
                            rows: model.onguardArtifacts.map {
                                ($0.artifactType, "\($0.clientID) · \($0.status) · \($0.labels.joined(separator: ", "))")
                            },
                        )
                    }
                }

                HStack(alignment: .top, spacing: 14) {
                    LiveCard(title: "Config Proposals", systemImage: "slider.horizontal.3") {
                        OnguardRows(
                            empty: "No config proposals.",
                            rows: model.onguardConfigs.map {
                                ($0.id, "\($0.clientID) · \($0.schemaName) · \($0.status)")
                            },
                        )
                    }
                    LiveCard(title: "Recent Events", systemImage: "bell.badge") {
                        OnguardRows(
                            empty: "No onguard events.",
                            rows: model.onguardEvents.prefix(20).map {
                                ($0.eventType, "\($0.clientID)" + ($0.acknowledgedBy.isEmpty ? "" : " · acked by \($0.acknowledgedBy)"))
                            },
                        )
                    }
                }
            }
            .padding(24)
        }
    }
}

private struct OnguardRows: View {
    let empty: String
    let rows: [(String, String)]

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if rows.isEmpty {
                Text(empty)
                    .foregroundStyle(.secondary)
            }
            ForEach(Array(rows.enumerated()), id: \.offset) { _, row in
                VStack(alignment: .leading, spacing: 3) {
                    Text(row.0.isEmpty ? "(unnamed)" : row.0)
                        .font(.headline)
                    Text(row.1.isEmpty ? "(no detail)" : row.1)
                        .font(.caption.monospaced())
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct TrustView: View {
    @EnvironmentObject private var model: CapDepAppModel
    @State private var groupID = "trusted-recipients"
    @State private var principalID = ""
    @State private var patternAction = "SEND_EMAIL"
    @State private var patternTarget = ""
    @State private var bindingName = ""
    @State private var bindingScope = "file:///Users/marc/Documents/**"
    @State private var bindingCategory = "personal"
    @State private var bindingTier = "sensitive"

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("Trust Controls")
                    .font(.largeTitle.weight(.bold))

                LiveCard(title: "Relationship Groups", systemImage: "person.2") {
                    VStack(alignment: .leading, spacing: 10) {
                        if model.relationshipGroups.isEmpty {
                            Text("No groups loaded. Add only principals you would trust for recurring low-risk flows.")
                                .foregroundStyle(.secondary)
                        }
                        ForEach(model.relationshipGroups) { group in
                            VStack(alignment: .leading, spacing: 4) {
                                Text(group.id)
                                    .font(.headline)
                                Text(group.members.isEmpty ? "(no members)" : group.members.joined(separator: ", "))
                                    .font(.caption.monospaced())
                                    .foregroundStyle(.secondary)
                                    .textSelection(.enabled)
                            }
                        }
                        Divider()
                        TextField("Group ID", text: $groupID)
                        TextField("principal@example.com", text: $principalID)
                        Button("Add Member") {
                            Task {
                                await model.addRelationshipMember(
                                    groupID: groupID,
                                    principalID: principalID,
                                )
                                principalID = ""
                            }
                        }
                        .disabled(groupID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || principalID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    }
                }

                LiveCard(title: "Approval Patterns", systemImage: "checklist.checked") {
                    VStack(alignment: .leading, spacing: 10) {
                        Text("Patterns are intentionally narrow and expire after 30 days or less. Broad wildcards are rejected by the daemon.")
                            .foregroundStyle(.secondary)
                        ForEach(model.approvalPatterns) { pattern in
                            VStack(alignment: .leading, spacing: 4) {
                                Text("\(pattern.action) -> \(pattern.targetPattern)")
                                    .font(.headline)
                                Text("Created by \(pattern.createdBy)")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                        Divider()
                        Picker("Action", selection: $patternAction) {
                            Text("Send email").tag("SEND_EMAIL")
                            Text("Queue purchase").tag("QUEUE_PURCHASE")
                            Text("Execute destructive").tag("EXECUTE_DESTRUCTIVE")
                        }
                        TextField("target@example.com or *@example.com", text: $patternTarget)
                        Button("Create 30-Day Pattern") {
                            Task {
                                await model.createApprovalPattern(
                                    action: patternAction,
                                    targetPattern: patternTarget,
                                )
                                patternTarget = ""
                            }
                        }
                        .disabled(patternTarget.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    }
                }

                LiveCard(title: "Source Bindings", systemImage: "tag") {
                    VStack(alignment: .leading, spacing: 10) {
                        Text("Source bindings are daemon-owned and affect IFC labels, trust, and write discipline. Keep scopes narrow.")
                            .foregroundStyle(.secondary)
                        if model.sourceBindings.isEmpty {
                            Text("No source bindings configured.")
                                .foregroundStyle(.secondary)
                        }
                        ForEach(model.sourceBindings) { binding in
                            HStack(alignment: .top) {
                                VStack(alignment: .leading, spacing: 3) {
                                    Text(binding.name)
                                        .font(.headline)
                                    Text(binding.scopePatternCanonical)
                                        .font(.caption.monospaced())
                                        .foregroundStyle(.secondary)
                                    Text("\(binding.category) · \(binding.defaultTier) · \(binding.writeDiscipline)")
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }
                                Spacer()
                                Button("Delete") {
                                    Task {
                                        await model.deleteSourceBinding(binding)
                                    }
                                }
                            }
                        }
                        Divider()
                        TextField("Binding name", text: $bindingName)
                        TextField("Canonical scope, e.g. file:///Users/me/Documents/Finance/**", text: $bindingScope)
                        TextField("Category", text: $bindingCategory)
                        Picker("Tier", selection: $bindingTier) {
                            Text("none").tag("none")
                            Text("sensitive").tag("sensitive")
                            Text("regulated").tag("regulated")
                            Text("restricted").tag("restricted")
                            Text("prohibited").tag("prohibited")
                        }
                        Button("Save Source Binding") {
                            Task {
                                await model.upsertSourceBinding(
                                    name: bindingName,
                                    scopePattern: bindingScope,
                                    category: bindingCategory,
                                    tier: bindingTier,
                                )
                                bindingName = ""
                            }
                        }
                        .disabled(
                            bindingName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                                || bindingScope.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                                || bindingCategory.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                        )
                    }
                }
            }
            .padding(24)
        }
    }
}

private struct SetupAssistantView: View {
    @EnvironmentObject private var model: CapDepAppModel

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("Setup Assistant")
                    .font(.largeTitle.weight(.bold))
                if model.setupChecks.isEmpty {
                    SetupRow(
                        title: "Daemon",
                        status: model.connected ? "Connected" : "Offline",
                        systemImage: model.connected ? "checkmark.circle" : "xmark.octagon",
                        ok: model.connected,
                    )
                }
                ForEach(model.setupChecks) { check in
                    SetupRow(
                        title: check.title,
                        status: check.detail,
                        systemImage: check.systemImage,
                        ok: check.status == "ok",
                        actions: check.actions,
                    )
                    .environmentObject(model)
                }
            }
            .padding(24)
        }
    }
}

private struct ProvenanceView: View {
    @EnvironmentObject private var model: CapDepAppModel

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("Provenance DAG")
                    .font(.largeTitle.weight(.bold))
                Text("Materialized sources and transformations from audit events. Use this to explain why an output is trusted, tainted, or blocked.")
                    .foregroundStyle(.secondary)
                HStack(alignment: .top, spacing: 14) {
                    LiveCard(title: "Nodes", systemImage: "circle.grid.cross") {
                        VStack(alignment: .leading, spacing: 8) {
                            if model.provenanceNodes.isEmpty {
                                Text("No provenance nodes in the current audit tail.")
                                    .foregroundStyle(.secondary)
                            }
                            ForEach(model.provenanceNodes) { node in
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(node.id)
                                        .font(.caption.monospaced().weight(.semibold))
                                    Text(node.kind.isEmpty ? "(unknown kind)" : node.kind)
                                        .foregroundStyle(.secondary)
                                    if !node.materializedID.isEmpty {
                                        Text(node.materializedID)
                                            .font(.caption.monospaced())
                                            .foregroundStyle(.secondary)
                                    }
                                }
                            }
                        }
                    }
                    LiveCard(title: "Edges", systemImage: "arrow.triangle.branch") {
                        VStack(alignment: .leading, spacing: 8) {
                            if model.provenanceEdges.isEmpty {
                                Text("No provenance edges in the current audit tail.")
                                    .foregroundStyle(.secondary)
                            }
                            ForEach(model.provenanceEdges) { edge in
                                Text("\(edge.from) -> \(edge.to)  \(edge.kind)")
                                    .font(.caption.monospaced())
                                    .textSelection(.enabled)
                            }
                        }
                    }
                }
            }
            .padding(24)
        }
    }
}

private struct SetupRow: View {
    @EnvironmentObject private var model: CapDepAppModel
    let title: String
    let status: String
    let systemImage: String
    let ok: Bool
    var actions: [SetupAction] = []

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: systemImage)
                .foregroundStyle(ok ? .green : .yellow)
                .font(.title2)
            VStack(alignment: .leading) {
                Text(title)
                    .font(.headline)
                Text(status)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            if let action = actions.first {
                Button(ok ? action.label : "Fix") {
                    Task {
                        await model.runSetupAction(action)
                    }
                }
                .disabled(!action.enabled)
            } else {
                Text(ok ? "Ready" : "Manual")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .padding()
        .background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 14))
    }
}

private struct RecentEventsView: View {
    @EnvironmentObject private var model: CapDepAppModel
    let limit: Int

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Recent Audit Events")
                .font(.title2.weight(.semibold))
            ForEach(model.events.prefix(limit)) { event in
                EventCard(event: event)
            }
        }
    }
}

private struct EventCard: View {
    let event: AuditEvent

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(event.eventType)
                .font(.headline)
            Text(event.payloadSummary.isEmpty ? event.sessionId : event.payloadSummary)
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(2)
        }
        .padding()
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 12))
    }
}

private struct InspectorView: View {
    @EnvironmentObject private var model: CapDepAppModel
    let selectedApproval: Approval?
    let selectedSession: CapDepSession?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("Inspector")
                    .font(.title.weight(.bold))
                if let approval = selectedApproval {
                    InspectorSection(title: "Approval", rows: [
                        ("Action", approval.action),
                        ("Target", approval.target),
                        ("Session", String(approval.fromSession.prefix(8))),
                    ])
                    InspectorSection(title: "Labels In", rows: approval.labelsIn.map { ("Label", $0) })
                } else if let session = selectedSession {
                    InspectorSection(title: "Session", rows: [
                        ("ID", String(session.id.prefix(8))),
                        ("Status", session.status),
                        ("Purpose", session.purpose.isEmpty ? "unset" : session.purpose),
                    ])
                    InspectorSection(title: "Labels", rows: session.labels.map { ("Label", $0) })
                    if let context = model.sessionSecurityContexts[session.id] {
                        SecurityContextSummaryView(context: context)
                    } else {
                        Button("Load Security Context") {
                            Task {
                                await model.refreshSecurityContext(sessionID: session.id)
                            }
                        }
                    }
                } else {
                    PlaceholderDetailView(
                        title: "Select an item",
                        systemImage: "sidebar.right",
                        message: "Select an approval or session to inspect labels, capabilities, policy trace, and provenance.",
                    )
                }
            }
            .padding(20)
        }
        .task(id: selectedSession?.id) {
            if let selectedSession {
                await model.refreshSecurityContext(sessionID: selectedSession.id)
            }
        }
    }
}

private struct SecurityContextSummaryView: View {
    let context: SessionSecurityContext

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            InspectorSection(title: "Security Context", rows: [
                ("Enforcement", context.enforcementMode),
                ("Labels", "\(context.labelCount)"),
                ("Capabilities", "\(context.activeCapabilityCount) active"),
                ("Used Kinds", context.usedKinds.isEmpty ? "(none)" : context.usedKinds.joined(separator: ", ")),
                ("Pending Approvals", "\(context.pendingApprovalCount)"),
                ("Policy Decisions", "\(context.policyDecisionCount) decisions, \(context.policyDenyCount) denies"),
                ("Policy Rules", context.matchedRuleIDs.isEmpty ? "(none)" : context.matchedRuleIDs.joined(separator: ", ")),
                ("Provenance", "\(context.provenanceNodeCount) nodes, \(context.provenanceEdgeCount) edges"),
                ("MCP Actors", context.externalMCPActors.isEmpty ? "(none)" : context.externalMCPActors.joined(separator: ", ")),
                ("Tool Actors", context.toolActors.isEmpty ? "(none)" : context.toolActors.joined(separator: ", ")),
                ("Onguard", context.onguardClientID.isEmpty ? "(none)" : context.onguardClientID),
            ])
            InspectorItemList(title: "Security Models", items: context.securityModels)
            InspectorItemList(title: "Flow Patterns", items: context.flowPatterns)
            InspectorSection(title: "Limitations", rows: context.limitations.map { ("Limit", $0) })
        }
    }
}

private struct InspectorItemList: View {
    let title: String
    let items: [SecurityContextItem]

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title)
                .font(.headline)
            if items.isEmpty {
                Text("(none)")
                    .foregroundStyle(.secondary)
            }
            ForEach(items) { item in
                VStack(alignment: .leading, spacing: 3) {
                    Label(
                        item.name,
                        systemImage: item.active ? "checkmark.shield" : "shield.slash",
                    )
                    .foregroundStyle(item.active ? .green : .secondary)
                    Text(item.evidenceSummary.isEmpty ? "(no evidence)" : item.evidenceSummary)
                        .font(.caption.monospaced())
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                }
            }
        }
        .padding()
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.quaternary.opacity(0.30), in: RoundedRectangle(cornerRadius: 12))
    }
}

private struct InspectorSection: View {
    let title: String
    let rows: [(String, String)]

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title)
                .font(.headline)
            if rows.isEmpty {
                Text("(none)")
                    .foregroundStyle(.secondary)
            }
            ForEach(Array(rows.enumerated()), id: \.offset) { _, row in
                VStack(alignment: .leading, spacing: 2) {
                    Text(row.0)
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.secondary)
                    Text(row.1.isEmpty ? "(empty)" : row.1)
                        .font(.caption.monospaced())
                        .textSelection(.enabled)
                }
            }
        }
        .padding()
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.quaternary.opacity(0.30), in: RoundedRectangle(cornerRadius: 12))
    }
}

private struct HeaderCard: View {
    @EnvironmentObject private var model: CapDepAppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("CapableDeputy")
                .font(.largeTitle.weight(.bold))
            Text("Ask from anywhere. Draft and organize easily. Require clear consent for sending, sharing, destructive writes, high-tier data, and generic desktop control.")
                .foregroundStyle(.secondary)
            Text("Socket: \(model.client.socketPath)")
                .font(.caption.monospaced())
                .foregroundStyle(.secondary)
        }
        .padding(24)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            LinearGradient(
                colors: [.blue.opacity(0.20), .green.opacity(0.10), .clear],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            ),
            in: RoundedRectangle(cornerRadius: 18)
        )
    }
}

private struct MetricCard: View {
    let title: String
    let value: String
    let systemImage: String

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Image(systemName: systemImage)
                .font(.title2)
            Text(value)
                .font(.largeTitle.weight(.bold))
            Text(title)
                .foregroundStyle(.secondary)
        }
        .padding()
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 14))
    }
}

private struct StatusFooter: View {
    @EnvironmentObject private var model: CapDepAppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Label(model.connected ? "Connected" : "Offline", systemImage: model.connected ? "checkmark.circle" : "xmark.octagon")
                .foregroundStyle(model.connected ? .green : .red)
            if let error = model.lastError {
                Text(error)
                    .font(.caption2)
                    .foregroundStyle(.red)
                    .lineLimit(3)
            }
        }
        .font(.caption)
        .padding()
    }
}

struct PlaceholderDetailView: View {
    let title: String
    let systemImage: String
    let message: String

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label(title, systemImage: systemImage)
                .font(.title3.weight(.semibold))
            Text(message)
                .foregroundStyle(.secondary)
        }
        .padding()
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.quaternary.opacity(0.30), in: RoundedRectangle(cornerRadius: 14))
    }
}

struct LiveCard<Content: View>: View {
    let title: String
    let systemImage: String
    let content: Content

    init(title: String, systemImage: String, @ViewBuilder content: () -> Content) {
        self.title = title
        self.systemImage = systemImage
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label(title, systemImage: systemImage)
                .font(.title3.weight(.semibold))
            content
        }
        .padding()
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.quaternary.opacity(0.30), in: RoundedRectangle(cornerRadius: 14))
    }
}
