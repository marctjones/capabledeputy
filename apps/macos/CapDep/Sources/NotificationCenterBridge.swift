import Foundation
import UserNotifications

/// #332 — stable identifiers shared by the notification bridge (which posts and
/// categorizes) and the delegate (which handles taps/actions). An actionable
/// approval notification is the surface that reliably reaches the user when NO
/// CapDep window is open, satisfying "with the main window closed, a new
/// approval still pops an actionable prompt."
enum ApprovalNotification {
    static let category = "CAPDEP_APPROVAL"
    /// Opens the full approval card (foreground) — approving happens there, with
    /// payload + labels visible, never blind from the banner.
    static let reviewAction = "CAPDEP_APPROVAL_REVIEW"
    /// Deny straight from the banner — safe/fail-closed without the full card.
    static let denyAction = "CAPDEP_APPROVAL_DENY"
}

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
        registerApprovalCategory()
        do {
            _ = try await UNUserNotificationCenter.current().requestAuthorization(
                options: [.alert, .sound, .badge],
            )
        } catch {
            // Notification permission is advisory. The daemon and GUI stay usable without it.
        }
    }

    /// Register the actionable approval category so the notification carries
    /// Review + Deny buttons. Idempotent (setNotificationCategories replaces).
    private func registerApprovalCategory() {
        let review = UNNotificationAction(
            identifier: ApprovalNotification.reviewAction,
            title: "Review",
            options: [.foreground],
        )
        let deny = UNNotificationAction(
            identifier: ApprovalNotification.denyAction,
            title: "Deny",
            options: [.destructive],
        )
        let category = UNNotificationCategory(
            identifier: ApprovalNotification.category,
            actions: [review, deny],
            intentIdentifiers: [],
            options: [],
        )
        UNUserNotificationCenter.current().setNotificationCategories([category])
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
        content.categoryIdentifier = ApprovalNotification.category
        let request = UNNotificationRequest(
            identifier: "capdep.pending-approval.\(approvalID)",
            content: content,
            trigger: nil,
        )
        try? await UNUserNotificationCenter.current().add(request)
    }

    func notifyOnguard(_ notification: OnguardNotificationViewData) async {
        guard canUseUserNotifications else {
            return
        }
        guard !notification.id.isEmpty else {
            return
        }
        let content = UNMutableNotificationContent()
        content.title = notification.title.isEmpty ? "CapDep onguard update" : notification.title
        content.body = notification.body.isEmpty
            ? notification.deepLink
            : notification.body
        content.sound = notification.urgency == "high" ? .defaultCritical : .default
        var userInfo: [String: Any] = [
            "onguard_notification_id": notification.id,
            "notification_class": notification.notificationClass,
            "deep_link": notification.deepLink,
        ]
        if let artifactRef = notification.artifactRef {
            userInfo["artifact_ref"] = artifactRef
        }
        if let approvalID = notification.approvalID {
            userInfo["approval_id"] = approvalID
        }
        content.userInfo = userInfo
        let request = UNNotificationRequest(
            identifier: "capdep.onguard.\(notification.id)",
            content: content,
            trigger: nil,
        )
        try? await UNUserNotificationCenter.current().add(request)
    }

    private var canUseUserNotifications: Bool {
        Bundle.main.bundleURL.pathExtension == "app"
    }
}
