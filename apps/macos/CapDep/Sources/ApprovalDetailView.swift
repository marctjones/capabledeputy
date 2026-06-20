import SwiftUI

struct ApprovalDetailView: View {
    @EnvironmentObject private var model: CapDepAppModel
    let approval: Approval
    private var detail: ApprovalDetail? {
        model.approvalDetails[approval.id]
    }

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

                RiskExplanation(approval: approval, detail: detail)

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

                DetailSection(title: "Labels Out") {
                    LabelList(labels: approval.labelsOut)
                }

                if !approval.rule.isEmpty || detail != nil {
                    DetailSection(title: "Policy Reason") {
                        VStack(alignment: .leading, spacing: 6) {
                            if !approval.rule.isEmpty {
                                Text("Rule: \(approval.rule)")
                                    .font(.caption.monospaced())
                                    .textSelection(.enabled)
                            }
                            Text(detail?.plainPolicyReason ?? "The daemon requested explicit approval for this action.")
                                .foregroundStyle(.secondary)
                        }
                    }
                }

                DetailSection(title: "Payload") {
                    Text(approval.payload.isEmpty ? "(empty)" : approval.payload)
                        .font(.body.monospaced())
                        .textSelection(.enabled)
                        .padding()
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(.black.opacity(0.06), in: RoundedRectangle(cornerRadius: 12))
                }

                if let detail, !detail.suggestedActions.isEmpty {
                    DetailSection(title: "Safer Paths") {
                        VStack(alignment: .leading, spacing: 8) {
                            ForEach(detail.suggestedActions) { action in
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(action.title)
                                        .font(.subheadline.weight(.semibold))
                                    Text(action.detail)
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }
                            }
                        }
                    }
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

                    Button("Defer") {
                        Task {
                            await model.deferApproval(approval)
                        }
                    }

                    if detail?.siblingApprovable == true {
                        Button("Approve Group (\(detail?.siblingPendingCount ?? 0))") {
                            Task {
                                await model.approveGroup(approval)
                            }
                        }
                    }

                    Spacer()

                    Button("Add Relationship") {
                        Task {
                            await model.addRelationshipMember(
                                groupID: "trusted-recipients",
                                principalID: approval.target,
                            )
                        }
                    }
                    .disabled(approval.target.isEmpty || approval.action != "SEND_EMAIL")

                    Button("Create Narrow Pattern") {
                        Task {
                            await model.createApprovalPattern(
                                action: approval.action,
                                targetPattern: approval.target,
                            )
                        }
                    }
                    .disabled(approval.target.isEmpty)

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
        .task(id: approval.id) {
            await model.refreshApprovalDetail(approval)
        }
    }
}

private struct RiskExplanation: View {
    let approval: Approval
    let detail: ApprovalDetail?

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("What will happen")
                .font(.headline)
            Text(detail?.effectText ?? fallbackEffectText)
                .foregroundStyle(.secondary)
            Text("This app does not soften policy. Approving relays your explicit decision to the daemon, which performs any declassification or one-shot execution path.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .padding()
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.yellow.opacity(0.12), in: RoundedRectangle(cornerRadius: 14))
    }

    private var fallbackEffectText: String {
        switch approval.action {
        case "SEND_EMAIL":
            return "The daemon may execute a scoped email send in a fresh approved session."
        case "QUEUE_PURCHASE":
            return "The daemon may queue a purchase using the approved payload and target."
        case "EXECUTE_DESTRUCTIVE":
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
