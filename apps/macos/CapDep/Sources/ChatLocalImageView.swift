import AppKit
import SwiftUI

struct ChatLocalImageView: View {
    let resolved: ChatImageURLResolver.ResolvedImage
    var onContentSizeChange: (() -> Void)?
    var onLoadFailed: (() -> Void)?

    @State private var staticImage: NSImage?
    @State private var loadFailed = false

    var body: some View {
        Group {
            if resolved.isAnimatedGIF, resolved.url.isFileURL {
                AnimatedGIFFileView(url: resolved.url, onContentSizeChange: onContentSizeChange)
            } else if let staticImage {
                Image(nsImage: staticImage)
                    .resizable()
                    .scaledToFit()
                    .frame(maxWidth: .infinity, maxHeight: 360, alignment: .leading)
                    .clipShape(RoundedRectangle(cornerRadius: 10))
                    .onAppear {
                        onContentSizeChange?()
                    }
            } else if loadFailed {
                EmptyView()
            } else {
                ProgressView()
                    .frame(maxWidth: .infinity, minHeight: 120)
            }
        }
        .task(id: resolved.url.absoluteString) {
            await loadImage()
        }
    }

    @MainActor
    private func loadImage() async {
        staticImage = nil
        loadFailed = false
        if resolved.isAnimatedGIF, resolved.url.isFileURL {
            return
        }
        if resolved.isRemote {
            do {
                let (data, _) = try await URLSession.shared.data(from: resolved.url)
                guard data.count <= ChatImageURLResolver.maxBytes else {
                    markLoadFailed()
                    return
                }
                guard let image = NSImage(data: data) else {
                    markLoadFailed()
                    return
                }
                staticImage = image
            } catch {
                markLoadFailed()
            }
            return
        }
        guard let image = NSImage(contentsOf: resolved.url) else {
            markLoadFailed()
            return
        }
        staticImage = image
    }

    @MainActor
    private func markLoadFailed() {
        loadFailed = true
        onLoadFailed?()
    }
}

private struct AnimatedGIFFileView: NSViewRepresentable {
    let url: URL
    var onContentSizeChange: (() -> Void)?

    func makeNSView(context: Context) -> NSImageView {
        let view = NSImageView()
        view.imageScaling = .scaleProportionallyUpOrDown
        view.animates = true
        view.setContentHuggingPriority(.defaultLow, for: .horizontal)
        view.setContentHuggingPriority(.defaultLow, for: .vertical)
        return view
    }

    func updateNSView(_ view: NSImageView, context: Context) {
        if view.image == nil {
            view.image = NSImage(contentsOf: url)
            DispatchQueue.main.async {
                onContentSizeChange?()
            }
        }
    }
}