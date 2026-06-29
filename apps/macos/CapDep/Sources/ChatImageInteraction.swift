import AppKit
@preconcurrency import Quartz
import SwiftUI

@MainActor
enum ChatImageInteraction {
    static func openInPreview(_ url: URL) {
        NSWorkspace.shared.open(url)
    }

    static func revealInFinder(_ url: URL) {
        guard url.isFileURL else {
            return
        }
        NSWorkspace.shared.activateFileViewerSelecting([url])
    }

    static func showQuickLook(_ url: URL) {
        ChatQuickLookCoordinator.shared.show(url: url)
    }
}

@MainActor
final class ChatQuickLookCoordinator: NSObject, QLPreviewPanelDataSource {
    static let shared = ChatQuickLookCoordinator()

    private var previewURL: URL?

    func show(url: URL) {
        previewURL = url
        guard let panel = QLPreviewPanel.shared() else {
            NSWorkspace.shared.open(url)
            return
        }
        panel.dataSource = self
        panel.makeKeyAndOrderFront(nil)
        panel.reloadData()
    }

    nonisolated func numberOfPreviewItems(in panel: QLPreviewPanel!) -> Int {
        MainActor.assumeIsolated {
            previewURL == nil ? 0 : 1
        }
    }

    nonisolated func previewPanel(_ panel: QLPreviewPanel!, previewItemAt index: Int) -> QLPreviewItem! {
        MainActor.assumeIsolated {
            previewURL as NSURL?
        }
    }
}

struct ChatImageInteractionModifier: ViewModifier {
    let url: URL

    func body(content: Content) -> some View {
        content
            .contentShape(Rectangle())
            .onTapGesture {
                ChatImageInteraction.showQuickLook(url)
            }
            .contextMenu {
                Button("Quick Look") {
                    ChatImageInteraction.showQuickLook(url)
                }
                Button("Open in Preview") {
                    ChatImageInteraction.openInPreview(url)
                }
                if url.isFileURL {
                    Button("Show in Finder") {
                        ChatImageInteraction.revealInFinder(url)
                    }
                }
            }
            .help("Click for Quick Look")
    }
}

extension View {
    func chatImageInteractions(for url: URL) -> some View {
        modifier(ChatImageInteractionModifier(url: url))
    }
}