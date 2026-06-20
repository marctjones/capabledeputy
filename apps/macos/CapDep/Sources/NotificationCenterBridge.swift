import Foundation
import UserNotifications

@MainActor
final class NotificationCenterBridge {
    private var permissionRequested = false

    func requestAuthorizationIfNeeded() async {
        guard !permissionRequested else {
            return
        }
        permissionRequested = true
        do {
            _ = try await UNUserNotificationCenter.current().requestAuthorization(
                options: [.alert, .sound, .badge],
            )
        } catch {
            // Notification permission is advisory. The daemon and GUI stay usable without it.
        }
    }

    func notifyPendingApprovals(count: Int) async {
        guard count > 0 else {
            return
        }
        let content = UNMutableNotificationContent()
        content.title = "CapDep approval needed"
        content.body = count == 1 ? "1 action is waiting for review." : "\(count) actions are waiting for review."
        content.sound = .default
        let request = UNNotificationRequest(
            identifier: "capdep.pending-approvals.\(Date().timeIntervalSince1970)",
            content: content,
            trigger: nil,
        )
        try? await UNUserNotificationCenter.current().add(request)
    }
}
