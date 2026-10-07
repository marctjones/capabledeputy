import AppKit
import SwiftUI
#if DEBUG
import Combine
#endif

/// Debug-only rendering mode for unattended accessibility validation. It changes
/// window presentation only; the production views and daemon authorization remain intact.
enum CapDepBackgroundGUIMode {
    static func diagnostic(_ event: String) {
        #if DEBUG
        guard enabled else { return }
        let line = "[background-gui] \(event)\n"
        try? FileHandle.standardError.write(contentsOf: Data(line.utf8))
        #endif
    }

    static var enabled: Bool {
        #if DEBUG
        ProcessInfo.processInfo.environment["CAPDEP_GUI_BACKGROUND_TEST"] == "1"
        #else
        false
        #endif
    }
}

@MainActor
func capdepOpenWindow(_ action: OpenWindowAction, id: String) {
    guard !CapDepBackgroundGUIMode.enabled else { return }
    action(id: id)
}

struct CapDepBackgroundDismissAction: @unchecked Sendable {
    let call: @MainActor () -> Void
}

private struct CapDepBackgroundDismissKey: EnvironmentKey {
    static let defaultValue: CapDepBackgroundDismissAction? = nil
}

extension EnvironmentValues {
    var capdepBackgroundDismiss: CapDepBackgroundDismissAction? {
        get { self[CapDepBackgroundDismissKey.self] }
        set { self[CapDepBackgroundDismissKey.self] = newValue }
    }
}

extension View {
    @ViewBuilder
    func capdepBackgroundSafeSheet<Content: View>(
        isPresented: Binding<Bool>, title: String = "Email Replies", @ViewBuilder content: @escaping () -> Content
    ) -> some View {
        #if DEBUG
        if CapDepBackgroundGUIMode.enabled {
            background(CapDepBackgroundSheetPresenter(isPresented: isPresented, title: title, content: content))
        } else {
            sheet(isPresented: isPresented, content: content)
        }
        #else
        sheet(isPresented: isPresented, content: content)
        #endif
    }

    @ViewBuilder
    func capdepBackgroundSafeSheet<Item: Identifiable, Content: View>(
        item: Binding<Item?>, title: String = "Email Replies", @ViewBuilder content: @escaping (Item) -> Content
    ) -> some View {
        #if DEBUG
        if CapDepBackgroundGUIMode.enabled {
            background(CapDepBackgroundSheetPresenter(
                isPresented: Binding(get: { item.wrappedValue != nil }, set: { if !$0 { item.wrappedValue = nil } }),
                title: title,
                content: { Group { if let value = item.wrappedValue { content(value) } } }
            ))
        } else {
            sheet(item: item, content: content)
        }
        #else
        sheet(item: item, content: content)
        #endif
    }
}

#if DEBUG
/// All test surfaces refuse both app-main and keyboard-key window status. The
/// nonactivating panel style also prevents accessibility actions from activating
/// the application. No frontmost, keyboard, or mouse synthesis is used.
@MainActor
final class CapDepBackgroundPanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }

    init(title: String, size: NSSize) {
        super.init(
            contentRect: NSRect(origin: NSPoint(x: 40, y: 80), size: size),
            styleMask: [.titled, .closable, .resizable, .nonactivatingPanel],
            backing: .buffered, defer: false
        )
        self.title = title
        hidesOnDeactivate = false
        isFloatingPanel = false
        isReleasedWhenClosed = false
        level = .normal
        collectionBehavior = [.managed, .ignoresCycle]
    }

    override func makeKeyAndOrderFront(_ sender: Any?) {
        orderBack(sender)
    }
}

@MainActor
final class CapDepBackgroundGUITestHost {
    private var chat: CapDepBackgroundPanel
    private var approval: CapDepBackgroundPanel?
    private var presentedApprovalID: Int?
    private var sessionToReopen = ProcessInfo.processInfo.environment["CAPDEP_GUI_TEST_REOPEN_SESSION"]
    private var observers: Set<AnyCancellable> = []
    private let model: CapDepAppModel

    init(model: CapDepAppModel) {
        self.model = model
        CapDepBackgroundGUIMode.diagnostic("host.init appHidden=\(NSApp.isHidden) active=\(NSApp.isActive) socket=\(model.client.socketPath)")
        chat = CapDepBackgroundPanel(title: "CapDep", size: NSSize(width: 920, height: 740))
        chat.contentViewController = NSHostingController(rootView: NavigationStack {
            ChatView().environmentObject(model)
        })
        chat.orderBack(nil)
        CapDepBackgroundGUIMode.diagnostic("host.ordered visible=\(chat.isVisible) number=\(chat.windowNumber) appWindows=\(NSApp.windows.count)")
        Task { @MainActor in
            CapDepBackgroundGUIMode.diagnostic("model.start.begin")
            await model.start()
            CapDepBackgroundGUIMode.diagnostic("model.start.end connected=\(model.connected)")
        }
        model.$connected.combineLatest(model.$lastError)
            .receive(on: RunLoop.main)
            .sink { connected, error in
                CapDepBackgroundGUIMode.diagnostic("model.connection=\(connected) error=\(error ?? "none")")
            }.store(in: &observers)
        model.$approvalWindowID.combineLatest(model.$pendingApprovals)
            .receive(on: RunLoop.main)
            .sink { [weak self] selected, pending in
                MainActor.assumeIsolated {
                    self?.showApproval(pending.first(where: { $0.id == selected }))
                }
            }.store(in: &observers)
        model.$sessions.receive(on: RunLoop.main).sink { [weak self] sessions in
            MainActor.assumeIsolated {
                guard let self, let id = self.sessionToReopen,
                      let session = sessions.first(where: { $0.id == id }) else { return }
                self.sessionToReopen = nil
                self.model.focusSession(session)
            }
        }.store(in: &observers)
    }

    private func showApproval(_ selected: Approval?) {
        guard let selected else {
            approval?.close()
            approval = nil
            presentedApprovalID = nil
            return
        }
        guard presentedApprovalID != selected.id else { return }
        presentedApprovalID = selected.id
        let panel = approval ?? CapDepBackgroundPanel(title: "Approval", size: NSSize(width: 760, height: 820))
        panel.contentViewController = NSHostingController(rootView: ApprovalDetailView(approval: selected).environmentObject(model))
        approval = panel
        panel.orderBack(nil)
    }
}

@MainActor
private struct CapDepBackgroundSheetPresenter<Content: View>: NSViewRepresentable {
    @Binding var isPresented: Bool
    let title: String
    let content: () -> Content

    final class Coordinator {
        var panel: CapDepBackgroundPanel?
    }

    func makeCoordinator() -> Coordinator { Coordinator() }
    func makeNSView(context: Context) -> NSView { NSView(frame: .zero) }

    func updateNSView(_ nsView: NSView, context: Context) {
        guard isPresented else {
            context.coordinator.panel?.close()
            context.coordinator.panel = nil
            return
        }
        let panel = context.coordinator.panel ?? CapDepBackgroundPanel(
            title: title, size: NSSize(width: 760, height: 780)
        )
        if context.coordinator.panel == nil {
            // Preserve the real view's state across ordinary SwiftUI updates.
            panel.contentViewController = NSHostingController(rootView: content().environment(
                \.capdepBackgroundDismiss,
                CapDepBackgroundDismissAction(call: { isPresented = false })
            ))
            context.coordinator.panel = panel
            panel.orderBack(nil)
        }
    }

    static func dismantleNSView(_ nsView: NSView, coordinator: Coordinator) {
        coordinator.panel?.close()
        coordinator.panel = nil
    }
}
#endif

/// Explicit debug launcher path: SwiftUI can postpone scene creation for an
/// application opened in the background. The host still renders the production
/// views and uses the same model, socket client, policy and approval callbacks.
public enum CapDepBackgroundGUIEntrypoint {
    @MainActor
    public static func runIfEnabled() -> Bool {
        #if DEBUG
        guard CapDepBackgroundGUIMode.enabled else { return false }
        CapDepBackgroundGUIMode.diagnostic("entrypoint.start")
        let instance = SingleInstanceGuard(
            name: SingleInstanceGuard.lockName(socketPath: DaemonClient.defaultSocketPath())
        )
        guard instance.didAcquire else {
            CapDepBackgroundGUIMode.diagnostic("entrypoint.existing-instance")
            return true
        }
        let application = NSApplication.shared
        application.setActivationPolicy(.accessory)
        let delegate = CapDepScriptingDelegate()
        application.delegate = delegate
        withExtendedLifetime((delegate, instance)) {
            application.run()
        }
        return true
        #else
        return false
        #endif
    }
}
