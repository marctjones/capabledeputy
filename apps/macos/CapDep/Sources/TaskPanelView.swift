import SwiftUI

struct TaskPanelView: View {
    @EnvironmentObject private var model: CapDepAppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack {
                VStack(alignment: .leading) {
                    Text("Current Task")
                        .font(.title.weight(.bold))
                    Text(model.selectedPurpose.rawValue.capitalized)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Toggle("Pinned", isOn: $model.taskPanelPinned)
                    .toggleStyle(.switch)
            }

            VStack(alignment: .leading, spacing: 8) {
                Text("Goal")
                    .font(.headline)
                Text(model.commandText.isEmpty ? "No active command yet." : model.commandText)
                    .foregroundStyle(model.commandText.isEmpty ? .secondary : .primary)
            }
            .padding()
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 14))

            VStack(alignment: .leading, spacing: 10) {
                Text("Attached Context")
                    .font(.headline)
                ContextChipRow(chips: model.contextChips)
            }

            if model.isRunningTurn, !model.turnStatusLine.isEmpty {
                HStack(spacing: 8) {
                    ProgressView()
                        .controlSize(.small)
                    Text(model.turnStatusLine)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            if !model.currentAssistantOutput.isEmpty {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Result")
                        .font(.headline)
                    Text(model.currentAssistantOutput)
                        .font(.body)
                        .textSelection(.enabled)
                        .lineLimit(8)
                }
                .padding()
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(.blue.opacity(0.08), in: RoundedRectangle(cornerRadius: 14))
            }

            if !model.turnPendingApprovalIDs.isEmpty {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Awaiting Your Approval")
                        .font(.headline)
                    Text(
                        "This turn paused on approval(s): "
                            + model.turnPendingApprovalIDs.map { "#\($0)" }.joined(separator: ", ")
                    )
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    Button("Review Approvals") {
                        model.selectedSection = .approvals
                        if let first = model.turnPendingApprovalIDs.first {
                            model.focusedApprovalID = first
                            model.approvalWindowID = first
                        }
                    }
                    .buttonStyle(.borderedProminent)
                }
                .padding()
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(.orange.opacity(0.12), in: RoundedRectangle(cornerRadius: 14))
            }

            if !model.currentToolOutcomes.isEmpty {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Tool Outcomes")
                        .font(.headline)
                    ForEach(model.currentToolOutcomes.prefix(5)) { outcome in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(outcome.toolName.isEmpty ? outcome.decision : "\(outcome.toolName): \(outcome.decision)")
                                .font(.caption.weight(.semibold))
                            Text(outcome.error.isEmpty ? outcome.reason : outcome.error)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .lineLimit(2)
                        }
                    }
                }
            }

            VStack(alignment: .leading, spacing: 10) {
                Text("Recent Activity")
                    .font(.headline)
                ForEach(model.events.prefix(8)) { event in
                    HStack(alignment: .top) {
                        Image(systemName: icon(for: event.eventType))
                            .foregroundStyle(color(for: event.eventType))
                        VStack(alignment: .leading) {
                            Text(event.eventType)
                                .font(.subheadline.weight(.semibold))
                            Text(event.payloadSummary.isEmpty ? event.sessionId : event.payloadSummary)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .lineLimit(2)
                        }
                    }
                }
            }

            Spacer()

            HStack {
                Button(role: .destructive) {
                    Task {
                        await model.cancelCurrentTurn()
                    }
                } label: {
                    Label("Stop", systemImage: "stop.circle")
                }
                .disabled(!model.isRunningTurn && model.currentSessionID == nil)

                Button("Pause") {
                    Task {
                        await model.pauseCurrentSession()
                    }
                }
                .disabled(model.currentSessionID == nil)

                Button("Resume") {
                    Task {
                        await model.resumeCurrentSession()
                    }
                }
                .disabled(model.currentSessionID == nil)

                Button("Abort") {
                    Task {
                        await model.abortCurrentSession()
                    }
                }
                .disabled(model.currentSessionID == nil)

                Spacer()
                Button("Trace") {
                    model.selectedSection = .policyTrace
                }
            }
        }
        .padding(22)
    }

    private func icon(for eventType: String) -> String {
        if eventType.contains("approval") {
            return "hand.raised"
        }
        if eventType.contains("policy") {
            return "shield"
        }
        if eventType.contains("denied") {
            return "xmark.octagon"
        }
        return "circle"
    }

    private func color(for eventType: String) -> Color {
        if eventType.contains("denied") {
            return .red
        }
        if eventType.contains("approval") {
            return .yellow
        }
        if eventType.contains("policy") {
            return .blue
        }
        return .secondary
    }
}
