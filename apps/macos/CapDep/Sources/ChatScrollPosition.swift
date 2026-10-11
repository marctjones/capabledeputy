import Foundation

/// Content growth changes the bottom, not the reader's scroll offset. Only an
/// offset change updates follow intent; append-only updates retain that intent.
struct ChatScrollPosition: Equatable {
    let top: CGFloat
    let bottom: CGFloat

    func shouldFollow(previousTop: CGFloat?, viewportHeight: CGFloat, wasFollowing: Bool) -> Bool {
        guard let previousTop else { return wasFollowing }
        guard abs(top - previousTop) > 1 else { return wasFollowing }
        return bottom <= viewportHeight + 48
    }
}
