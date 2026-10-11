import Foundation

/// Wording for the menu bar status line.
///
/// A socket ping proves the daemon answered, nothing more. The status must state
/// connection facts and the count of items waiting on the user; it must never
/// imply the system is "protected" or "safe" (design: capable-deputy-v1 §status).
enum MenuBarStatus {
    static func title(connected: Bool, pendingCount: Int, disconnectedTitle: String) -> String {
        guard connected else { return disconnectedTitle }
        switch pendingCount {
        case 0: return "Connected"
        case 1: return "Connected · 1 needs review"
        default: return "Connected · \(pendingCount) need review"
        }
    }
}
