import AppKit
import Foundation
import UserNotifications

final class CapDepNotificationDelegate: NSObject, UNUserNotificationCenterDelegate {
    weak var model: CapDepAppModel?

    /// Show the banner even while CapDep is frontmost, so an approval that
    /// arrives with only the menu bar visible is not silently swallowed (#332).
    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
    ) async -> UNNotificationPresentationOptions {
        [.banner, .sound, .list]
    }

    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse,
    ) async {
        let userInfo = response.notification.request.content.userInfo
        let approvalID: Int?
        if let rawID = userInfo["approval_id"] as? Int {
            approvalID = rawID
        } else if let rawID = userInfo["approval_id"] as? NSNumber {
            approvalID = rawID.intValue
        } else {
            approvalID = nil
        }
        guard let approvalID else {
            return
        }
        let action = response.actionIdentifier
        let target = model
        await MainActor.run {
            // Every action, including the legacy Deny identifier from banners
            // delivered before it was removed, opens the review: no decision is
            // ever made from a banner. Activating makes the always-alive
            // menu-bar observer's openWindow surface the window even when every
            // CapDep window was closed.
            _ = action
            NSApplication.shared.activate(ignoringOtherApps: true)
            target?.presentApproval(id: approvalID)
        }
    }
}
