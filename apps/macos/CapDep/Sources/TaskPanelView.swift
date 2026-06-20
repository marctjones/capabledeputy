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
                    model.commandText = ""
                } label: {
                    Label("Stop", systemImage: "stop.circle")
                }
                Button("Pause") {}
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
