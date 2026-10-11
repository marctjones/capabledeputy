import Foundation

/// Chooses which approval a "needs review" notification should open.
///
/// The daemon lists pending approvals oldest-first, so notifying for `.first`
/// pointed the banner at an old, unrelated item whenever a new one arrived.
/// The banner must name the item that just arrived: the newest id the user has
/// not been told about yet.
enum ApprovalNotificationRouting {
    /// - Parameters:
    ///   - announced: approval ids the user has already been notified about.
    ///   - pending: currently pending approvals, in any order.
    /// - Returns: the newest unannounced approval id, or nil when nothing is new.
    static func approvalToAnnounce(announced: Set<Int>, pending: [Approval]) -> Int? {
        pending.map(\.id).filter { !announced.contains($0) }.max()
    }
}
