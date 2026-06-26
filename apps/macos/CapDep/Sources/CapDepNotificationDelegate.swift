import Foundation
import UserNotifications

final class CapDepNotificationDelegate: NSObject, UNUserNotificationCenterDelegate {
    weak var model: CapDepAppModel?

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
        let target = model
        await MainActor.run {
            target?.presentApproval(id: approvalID)
        }
    }
}