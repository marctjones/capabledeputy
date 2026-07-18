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
            switch action {
            case ApprovalNotification.denyAction:
                // Fail-closed decision straight from the banner.
                Task { await target?.denyApprovalByID(approvalID) }
            default:
                // Review action or a plain tap: bring CapDep forward and route
                // to the approval card. Activating makes the always-alive
                // menu-bar observer's openWindow surface the window even when
                // every CapDep window was closed.
                NSApplication.shared.activate(ignoringOtherApps: true)
                target?.presentApproval(id: approvalID)
            }
        }
    }
}
