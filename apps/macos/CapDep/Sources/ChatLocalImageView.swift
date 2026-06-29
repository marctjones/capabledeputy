import AppKit
import SwiftUI

struct ChatLocalImageView: View {
    let resolved: ChatImageURLResolver.ResolvedImage
    var onContentSizeChange: (() -> Void)?
    var onLoadFailed: (() -> Void)?

    @State private var staticImage: NSImage?
    @State private var animatedFileURL: URL?
    @State private var loadFailed = false

    var body: some View {
        Group {
            if resolved.isAnimatedGIF, let animatedFileURL {
                AnimatedGIFFileView(url: animatedFileURL, onContentSizeChange: onContentSizeChange)
                    .frame(maxWidth: .infinity, maxHeight: 360, alignment: .leading)
                    .chatImageInteractions(for: animatedFileURL)
            } else if let staticImage {
                Image(nsImage: staticImage)
                    .resizable()
                    .scaledToFit()
                    .frame(maxWidth: .infinity, maxHeight: 360, alignment: .leading)
                    .clipShape(RoundedRectangle(cornerRadius: 10))
                    .chatImageInteractions(for: interactionURL)
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

    private var interactionURL: URL {
        animatedFileURL ?? resolved.url
    }

    @MainActor
    private func loadImage() async {
        staticImage = nil
        animatedFileURL = nil
        loadFailed = false

        if resolved.isAnimatedGIF {
            if resolved.url.isFileURL {
                animatedFileURL = resolved.url
                return
            }
            do {
                let (data, _) = try await URLSession.shared.data(from: resolved.url)
                guard data.count <= ChatImageURLResolver.maxBytes else {
                    markLoadFailed()
                    return
                }
                let temp = FileManager.default.temporaryDirectory
                    .appendingPathComponent("capdep-gif-\(UUID().uuidString).gif")
                try data.write(to: temp)
                animatedFileURL = temp
            } catch {
                markLoadFailed()
            }
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