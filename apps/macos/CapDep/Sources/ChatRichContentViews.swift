import AppKit
import SwiftUI

struct ChatRichMessageBody: View {
    let text: String
    var isStreaming: Bool = false
    var authorizedImagePaths: Set<String> = []
    var holdUnverifiedGeneratedImages: Bool = false
    var onContentSizeChange: (() -> Void)?

    private var blocks: [ChatBlock] {
        ChatMarkdownParser.blocks(
            from: text,
            isStreaming: isStreaming,
            authorizedImagePaths: authorizedImagePaths,
            holdUnverifiedGeneratedImages: holdUnverifiedGeneratedImages,
        )
    }

    var body: some View {
        if isStreaming, text.isEmpty {
            Text("…")
                .font(.body)
                .foregroundStyle(.secondary)
        } else if blocks.isEmpty {
            Text(ChatContentFormatter.attributedMarkdown(from: text))
                .font(.body)
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
                .tint(.accentColor)
        } else {
            VStack(alignment: .leading, spacing: 10) {
                ForEach(Array(blocks.enumerated()), id: \.offset) { _, block in
                    switch block {
                    case .prose(let prose):
                        Text(ChatContentFormatter.attributedMarkdown(from: prose, fullDocument: true))
                            .font(.body)
                            .textSelection(.enabled)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .tint(.accentColor)
                    case .code(let language, let body):
                        ChatCodeBlockView(language: language, code: body)
                    case .image(let alt, let urlString):
                        ChatImageBlockView(
                            alt: alt,
                            urlString: urlString,
                            onContentSizeChange: onContentSizeChange,
                        )
                    }
                }
            }
        }
    }
}

struct ChatCodeBlockView: View {
    let language: String?
    let code: String

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                if let language, !language.isEmpty {
                    Text(language)
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.secondary)
                } else {
                    Text("Code")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button("Copy") {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(code, forType: .string)
                }
                .buttonStyle(.borderless)
                .font(.caption)
            }
            ScrollView(.horizontal, showsIndicators: true) {
                Text(code)
                    .font(.system(.body, design: .monospaced))
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .padding(12)
        .background(.black.opacity(0.06), in: RoundedRectangle(cornerRadius: 12))
    }
}

struct ChatImageBlockView: View {
    @EnvironmentObject private var model: CapDepAppModel

    let alt: String
    let urlString: String
    var onContentSizeChange: (() -> Void)?

    @State private var resolvedImage: ChatImageURLResolver.ResolvedImage?
    @State private var resolveFailure: ChatImageURLResolver.Failure?
    @State private var renderFailed = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if let resolvedImage, !renderFailed {
                ChatLocalImageView(
                    resolved: resolvedImage,
                    onContentSizeChange: onContentSizeChange,
                    onLoadFailed: {
                        renderFailed = true
                    },
                )
            } else if resolveFailure != nil || renderFailed {
                imageFallback
            } else {
                ProgressView()
                    .frame(maxWidth: .infinity, minHeight: 80)
            }
            if !alt.isEmpty {
                Text(alt)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .task(id: urlString) {
            renderFailed = false
            switch ChatImageURLResolver.resolve(urlString) {
            case .success(let resolved):
                resolvedImage = resolved
                resolveFailure = nil
                ChatDebugLog.log(
                    "image_resolve_ok",
                    metadata: [
                        "path": resolved.url.path,
                        "remote": String(resolved.isRemote),
                    ],
                )
            case .failure(let failure):
                resolvedImage = nil
                resolveFailure = failure
                ChatDebugLog.log(
                    "image_resolve_fail",
                    metadata: [
                        "url": urlString,
                        "reason": String(describing: failure),
                    ],
                )
            }
        }
        .onChange(of: renderFailed) { _, failed in
            if failed {
                ChatDebugLog.log(
                    "image_render_fail",
                    metadata: [
                        "url": urlString,
                        "resolved": resolvedImage?.url.path ?? "",
                    ],
                )
            }
        }
    }

    private var imageFallback: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label(failureTitle, systemImage: "photo")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
            Text(urlString)
                .font(.caption2.monospaced())
                .foregroundStyle(.secondary)
                .textSelection(.enabled)
            if let grantStep = matchingGrantRecoveryStep {
                Button("Allow access to view image") {
                    model.grantPromptPresented = true
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.small)
                Text("CapDep needs \(grantStep.grantKind ?? "READ_FS") permission for this file location.")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 10))
    }

    private var failureTitle: String {
        switch resolveFailure {
        case .tooLarge:
            return "Image too large to display"
        case .unsupportedFormat:
            return "Unsupported image format"
        case .notFound:
            return "Image not found"
        case .unreadable:
            return "Image unavailable (access denied)"
        default:
            return "Image unavailable"
        }
    }

    private var matchingGrantRecoveryStep: RecoveryStep? {
        guard let step = model.pendingGrantRecovery,
              step.grantKind == "READ_FS",
              let pattern = step.guiGrantPattern() ?? step.grantPattern else {
            return nil
        }
        let expanded = NSString(string: urlString).expandingTildeInPath
        guard ChatImageURLResolver.matchesGrantPattern(path: expanded, pattern: pattern) else {
            return nil
        }
        return step
    }
}