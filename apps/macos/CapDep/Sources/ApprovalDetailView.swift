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
                            .font(.title.weight(.semibold))
                        Text(approval.action)
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    StatusPill(text: approval.status)
                }

                RiskExplanation(approval: approval, detail: detail)

                ApprovalFlowSummary(approval: approval)

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

                if let artifact = detail?.reviewArtifact {
                    DetailSection(title: "Reviewed Artifact") {
                        VisualReviewArtifactCard(artifact: artifact)
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
                .controlSize(.large)
            }
            .padding(28)
        }
        .task(id: approval.id) {
            await model.refreshApprovalDetail(approval)
        }
    }
}

private struct VisualReviewArtifactCard: View {
    let artifact: ReviewArtifact

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                Label(
                    artifact.title.isEmpty ? artifact.displayKind : artifact.title,
                    systemImage: artifact.systemImage,
                )
                    .font(.headline)
                Spacer()
                Text(artifact.effect.replacingOccurrences(of: "_", with: " ").uppercased())
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
            }

            HStack(alignment: .top, spacing: 10) {
                FlowStep(
                    title: "Destination",
                    value: artifact.destinationID.isEmpty ? artifact.target : artifact.destinationID,
                    systemImage: "scope",
                )
                FlowStep(
                    title: "Hash",
                    value: artifact.shortHash.isEmpty ? "(missing)" : artifact.shortHash,
                    systemImage: "number",
                )
                FlowStep(
                    title: "Type",
                    value: artifact.displayKind,
                    systemImage: "doc.text",
                )
            }

            if !artifact.labels.isEmpty {
                LabelList(labels: artifact.labels)
            }

            Text(artifact.preview.isEmpty ? "(empty artifact preview)" : artifact.preview)
                .font(.body.monospaced())
                .textSelection(.enabled)
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(.black.opacity(0.06), in: RoundedRectangle(cornerRadius: 8))

            if artifact.previewTruncated {
                Label("Preview truncated; hash still binds the full artifact.", systemImage: "scissors")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.blue.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(.quaternary))
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
            Text("Approving sends your explicit decision to the daemon. Deny or defer if the source, action, destination, or payload is not what you expect.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.yellow.opacity(0.12), in: RoundedRectangle(cornerRadius: 8))
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

private struct ApprovalFlowSummary: View {
    let approval: Approval

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Review Flow")
                .font(.headline)
            HStack(alignment: .top, spacing: 10) {
                FlowStep(
                    title: "Source",
                    value: approval.fromSession.isEmpty ? "Current session" : "Session \(approval.fromSession.prefix(8))",
                    systemImage: "tray.and.arrow.down",
                )
                FlowArrow()
                FlowStep(
                    title: "Action",
                    value: approval.action.isEmpty ? "Approve requested action" : approval.action.replacingOccurrences(of: "_", with: " "),
                    systemImage: "gearshape",
                )
                FlowArrow()
                FlowStep(
                    title: "Destination",
                    value: approval.target.isEmpty ? "No explicit target" : approval.target,
                    systemImage: destinationIcon,
                )
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var destinationIcon: String {
        switch approval.action {
        case "SEND_EMAIL":
            return "envelope"
        case "QUEUE_PURCHASE":
            return "cart"
        case "EXECUTE_DESTRUCTIVE":
            return "exclamationmark.triangle"
        default:
            return "arrow.up.right"
        }
    }
}

private struct FlowStep: View {
    let title: String
    let value: String
    let systemImage: String

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            Label(title, systemImage: systemImage)
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
            Text(value)
                .font(.subheadline)
                .lineLimit(3)
                .textSelection(.enabled)
        }
        .padding(12)
        .frame(maxWidth: .infinity, minHeight: 86, alignment: .topLeading)
        .background(.quaternary.opacity(0.28), in: RoundedRectangle(cornerRadius: 8))
    }
}

private struct FlowArrow: View {
    var body: some View {
        Image(systemName: "chevron.right")
            .font(.caption.weight(.semibold))
            .foregroundStyle(.tertiary)
            .frame(width: 12, height: 86)
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
