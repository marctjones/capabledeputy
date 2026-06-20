import SwiftUI

struct ApprovalDetailView: View {
    @EnvironmentObject private var model: CapDepAppModel
    let approval: Approval

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                HStack(alignment: .firstTextBaseline) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("Approval #\(approval.id)")
                            .font(.largeTitle.weight(.bold))
                        Text(approval.action)
                            .font(.title2)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    StatusPill(text: approval.status)
                }

                RiskExplanation(approval: approval)

                DetailSection(title: "Target") {
                    Text(approval.target.isEmpty ? "(none)" : approval.target)
                        .font(.body.monospaced())
                        .textSelection(.enabled)
                }

                DetailSection(title: "Justification") {
                    Text(approval.justification.isEmpty ? "(none supplied)" : approval.justification)
                        .textSelection(.enabled)
                }

                DetailSection(title: "Labels In") {
                    LabelList(labels: approval.labelsIn)
                }

                DetailSection(title: "Payload") {
                    Text(approval.payload.isEmpty ? "(empty)" : approval.payload)
                        .font(.body.monospaced())
                        .textSelection(.enabled)
                        .padding()
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(.black.opacity(0.06), in: RoundedRectangle(cornerRadius: 12))
                }

                HStack {
                    Button(role: .destructive) {
                        Task {
                            await model.deny(approval)
                        }
                    } label: {
                        Label("Deny", systemImage: "xmark.circle")
                    }
                    .keyboardShortcut(.cancelAction)

                    Spacer()

                    Button {
                        Task {
                            await model.approve(approval)
                        }
                    } label: {
                        Label("Approve Once", systemImage: "checkmark.circle")
                    }
                    .buttonStyle(.borderedProminent)
                    .keyboardShortcut(.defaultAction)
                }
            }
            .padding(28)
        }
    }
}

private struct RiskExplanation: View {
    let approval: Approval

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("What will happen")
                .font(.headline)
            Text(effectText)
                .foregroundStyle(.secondary)
            Text("This app does not soften policy. Approving relays your explicit decision to the daemon, which performs any declassification or one-shot execution path.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .padding()
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.yellow.opacity(0.12), in: RoundedRectangle(cornerRadius: 14))
    }

    private var effectText: String {
        switch approval.action {
        case "send_email":
            return "The daemon may execute a scoped email send in a fresh approved session."
        case "queue_purchase":
            return "The daemon may queue a purchase using the approved payload and target."
        case "execute_destructive":
            return "The daemon may execute a destructive operation with a one-shot capability."
        default:
            return "The daemon will mark this approval as approved. Review the payload exactly before approving."
        }
    }
}

private struct DetailSection<Content: View>: View {
    let title: String
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title)
                .font(.headline)
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct LabelList: View {
    let labels: [String]

    var body: some View {
        if labels.isEmpty {
            Text("(none)")
                .foregroundStyle(.secondary)
        } else {
            FlowLayout(labels: labels)
        }
    }
}

private struct StatusPill: View {
    let text: String

    var body: some View {
        Text(text)
            .font(.caption.weight(.semibold))
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .background(.blue.opacity(0.15), in: Capsule())
    }
}

private struct FlowLayout: View {
    let labels: [String]

    var body: some View {
        HStack {
            ForEach(labels, id: \.self) { label in
                Text(label)
                    .font(.caption.monospaced())
                    .padding(.horizontal, 8)
                    .padding(.vertical, 5)
                    .background(.quaternary, in: Capsule())
            }
        }
    }
}
