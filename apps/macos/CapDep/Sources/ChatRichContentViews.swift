import AppKit
import SwiftUI

struct ChatRichMessageBody: View {
    let text: String
    var isStreaming: Bool = false
    var onContentSizeChange: (() -> Void)?

    private var blocks: [ChatBlock] {
        ChatMarkdownParser.blocks(from: text, isStreaming: isStreaming)
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
    let alt: String
    let urlString: String
    var onContentSizeChange: (() -> Void)?

    @State private var resolvedURL: URL?
    @State private var loadFailed = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if let resolvedURL {
                AsyncImage(url: resolvedURL) { phase in
                    switch phase {
                    case .empty:
                        ProgressView()
                            .frame(maxWidth: .infinity, minHeight: 120)
                    case .success(let image):
                        image
                            .resizable()
                            .scaledToFit()
                            .frame(maxWidth: .infinity, maxHeight: 360, alignment: .leading)
                            .clipShape(RoundedRectangle(cornerRadius: 10))
                            .onAppear {
                                onContentSizeChange?()
                            }
                    case .failure:
                        imageFallback
                    @unknown default:
                        imageFallback
                    }
                }
            } else if loadFailed {
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
            resolvedURL = ChatImageURLResolver.resolve(urlString)
            loadFailed = resolvedURL == nil
        }
    }

    private var imageFallback: some View {
        VStack(alignment: .leading, spacing: 4) {
            Label("Image unavailable", systemImage: "photo")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
            Text(urlString)
                .font(.caption2.monospaced())
                .foregroundStyle(.secondary)
                .textSelection(.enabled)
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 10))
    }
}

enum ChatImageURLResolver {
    static func resolve(_ raw: String) -> URL? {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            return nil
        }
        if trimmed.hasPrefix("https://") {
            return URL(string: trimmed)
        }
        if trimmed.hasPrefix("file://") {
            return URL(string: trimmed)
        }
        let expanded = NSString(string: trimmed).expandingTildeInPath
        if expanded.hasPrefix("/") {
            return URL(fileURLWithPath: expanded)
        }
        return nil
    }
}