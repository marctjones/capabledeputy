import SwiftUI

struct CapabilityGrantDetailView: View {
    @EnvironmentObject private var model: CapDepAppModel
    let step: RecoveryStep
    let outcome: ToolOutcome?
    let sessionID: String

    private var grantPattern: String {
        step.guiGrantPattern() ?? step.grantPattern ?? ""
    }

    private var canRetry: Bool {
        model.pendingGrantRetryMessage != nil
    }

    var body: some View {
        let isWebSearch = step.isWebSearchGrant
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                HStack(alignment: .firstTextBaseline) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(isWebSearch ? "Allow web search?" : "Allow access?")
                            .font(.largeTitle.weight(.bold))
                        if let kind = step.grantKind {
                            Text(PolicyPromptCopy.capabilityKindLabel(kind))
                                .font(.title2)
                                .foregroundStyle(.secondary)
                        }
                    }
                    Spacer()
                    StatusPill(text: "Capability")
                }

                VStack(alignment: .leading, spacing: 10) {
                    Text("What CapDep tried to do")
                        .font(.headline)
                    Text(PolicyPromptCopy.grantEffectText(kind: step.grantKind, toolName: outcome?.toolName))
                        .foregroundStyle(.secondary)
                    Text(
                        "This is a capability grant, not an approval queue item. "
                            + "You are extending what this session may read or touch — not approving one risky action.",
                    )
                    .font(.caption)
                    .foregroundStyle(.secondary)
                }
                .padding()
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(.yellow.opacity(0.12), in: RoundedRectangle(cornerRadius: 14))

                if !grantPattern.isEmpty {
                    DetailSection(title: "Location or scope") {
                        Text(grantPattern)
                            .font(.body.monospaced())
                            .textSelection(.enabled)
                    }
                }

                if let outcome, !outcome.toolName.isEmpty {
                    DetailSection(title: "Blocked tool") {
                        Text(outcome.toolName)
                            .font(.body.monospaced())
                    }
                }

                if let outcome, !outcome.reason.isEmpty || !outcome.error.isEmpty {
                    DetailSection(title: "Why it was blocked") {
                        Text(outcome.error.isEmpty ? outcome.reason : outcome.error)
                            .foregroundStyle(.secondary)
                            .textSelection(.enabled)
                    }
                }

                if !step.rationale.isEmpty {
                    DetailSection(title: "Recovery guidance") {
                        Text(step.rationale)
                            .foregroundStyle(.secondary)
                            .textSelection(.enabled)
                    }
                }

                DetailSection(title: "After you allow") {
                    Text(
                        PolicyPromptCopy.afterGrantText(isWebSearch: isWebSearch, canRetry: canRetry),
                    )
                    .foregroundStyle(.secondary)
                }

                HStack {
                    Button("Not now") {
                        model.dismissGrantPrompt()
                    }
                    .keyboardShortcut(.cancelAction)

                    Spacer()

                    if canRetry {
                        Button {
                            Task {
                                await model.grantCapabilityAndRetry(from: step, sessionID: sessionID)
                                model.dismissGrantPrompt()
                            }
                        } label: {
                            Label(
                                isWebSearch ? "Allow search & try again" : "Allow & try again",
                                systemImage: "arrow.clockwise.circle.fill",
                            )
                        }
                        .buttonStyle(.borderedProminent)
                        .keyboardShortcut(.defaultAction)
                    } else {
                        Button {
                            Task {
                                await model.grantCapability(from: step, sessionID: sessionID)
                                model.dismissGrantPrompt()
                            }
                        } label: {
                            Label(
                                isWebSearch ? "Allow web search" : "Allow access",
                                systemImage: "checkmark.circle.fill",
                            )
                        }
                        .buttonStyle(.borderedProminent)
                        .keyboardShortcut(.defaultAction)
                    }
                }
            }
            .padding(28)
        }
    }
}

enum PolicyPromptCopy {
    static func capabilityKindLabel(_ kind: String) -> String {
        switch kind {
        case "READ_FS":
            return "Read files"
        case "CREATE_FS":
            return "Create files"
        case "MODIFY_FS":
            return "Modify files"
        case "WRITE_FS":
            return "Write files"
        case "SEND_EMAIL":
            return "Send email"
        case "WEB_FETCH":
            return "Search and fetch web pages"
        case "GMAIL_READ":
            return "Read Gmail"
        case "CALENDAR_READ":
            return "Read calendar"
        case "DRIVE_READ":
            return "Read Google Drive"
        default:
            return kind.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    static func grantEffectText(kind: String?, toolName: String?) -> String {
        if kind == "WEB_FETCH" {
            if let toolName, !toolName.isEmpty {
                return "The assistant called \(toolName) to search or fetch the web, but this session does not yet have read-only web access."
            }
            return "This session does not yet have read-only web access for search or page fetches."
        }
        let action = capabilityKindLabel(kind ?? "access")
        if let toolName, !toolName.isEmpty {
            return "The assistant called \(toolName) but this session lacks \(action.lowercased()) permission for the requested target."
        }
        return "This session lacks \(action.lowercased()) permission for the requested target."
    }

    static func afterGrantText(isWebSearch: Bool, canRetry: Bool) -> String {
        if isWebSearch {
            return canRetry
                ? "CapDep will grant read-only web search access for this session and automatically retry your last message."
                : "CapDep will grant read-only web search access for this session. Ask again and the search should proceed."
        }
        return canRetry
            ? "CapDep will grant session access for this scope and automatically retry your last message."
            : "CapDep will grant session access for this scope. Ask again and the file or directory read should proceed."
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

private struct StatusPill: View {
    let text: String

    var body: some View {
        Text(text)
            .font(.caption.weight(.semibold))
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .background(.yellow.opacity(0.2), in: Capsule())
    }
}
