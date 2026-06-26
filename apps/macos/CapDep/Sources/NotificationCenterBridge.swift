import Foundation
import UserNotifications

@MainActor
final class NotificationCenterBridge {
    private var permissionRequested = false

    func requestAuthorizationIfNeeded() async {
        guard canUseUserNotifications else {
            return
        }
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

    func notifyPendingApproval(count: Int, approvalID: Int) async {
        guard canUseUserNotifications else {
            return
        }
        guard count > 0 else {
            return
        }
        let content = UNMutableNotificationContent()
        content.title = "CapDep approval needed"
        content.body = count == 1
            ? "Approval #\(approvalID) is waiting for review."
            : "\(count) actions are waiting for review. Open approval #\(approvalID)."
        content.sound = .default
        content.userInfo = ["approval_id": approvalID]
        let request = UNNotificationRequest(
            identifier: "capdep.pending-approval.\(approvalID)",
            content: content,
            trigger: nil,
        )
        try? await UNUserNotificationCenter.current().add(request)
    }

    private var canUseUserNotifications: Bool {
        Bundle.main.bundleURL.pathExtension == "app"
    }
}