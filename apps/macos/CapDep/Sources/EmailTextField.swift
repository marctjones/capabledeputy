import AppKit
import SwiftUI

/// Propagate assistive-technology edits even when this field never becomes the
/// first responder. AppKit's AXValue setter alone can leave a SwiftUI binding
/// unchanged, making displayed text differ from the text saved for review.
@MainActor
final class EmailAccessibleTextField: NSTextField {
    var valueChanged: ((String) -> Void)?

    override func setAccessibilityValue(_ value: Any?) {
        guard let text = value as? String, isEnabled, isEditable else { return }
        super.setAccessibilityValue(text)
        stringValue = text
        valueChanged?(text)
    }
}

struct EmailTextField: NSViewRepresentable {
    let title: String
    @Binding var text: String
    let identifier: String
    var enabled = true

    final class Coordinator: NSObject, NSTextFieldDelegate {
        var parent: EmailTextField
        init(_ parent: EmailTextField) { self.parent = parent }
        func controlTextDidChange(_ notification: Notification) {
            guard let field = notification.object as? NSTextField else { return }
            parent.text = field.stringValue
        }
    }

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeNSView(context: Context) -> EmailAccessibleTextField {
        let field = EmailAccessibleTextField()
        field.bezelStyle = .roundedBezel
        field.isBezeled = true
        field.isEditable = true
        field.isSelectable = true
        field.font = .systemFont(ofSize: NSFont.systemFontSize)
        field.delegate = context.coordinator
        field.setContentHuggingPriority(.defaultLow, for: .horizontal)
        field.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        return field
    }

    func updateNSView(_ field: EmailAccessibleTextField, context: Context) {
        context.coordinator.parent = self
        field.placeholderString = title
        field.setAccessibilityLabel(title)
        field.setAccessibilityIdentifier(identifier)
        field.isEnabled = enabled
        if field.stringValue != text { field.stringValue = text }
        field.valueChanged = { [weak coordinator = context.coordinator] value in
            coordinator?.parent.text = value
        }
    }
}
