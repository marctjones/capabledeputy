import SwiftUI

struct GrantCardWindow: View {
    @EnvironmentObject private var model: CapDepAppModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        Group {
            if
                model.grantPromptPresented,
                let step = model.pendingGrantRecovery,
                let sessionID = model.currentSessionID {
                CapabilityGrantDetailView(
                    step: step,
                    outcome: model.pendingDeniedGrantOutcome,
                    sessionID: sessionID,
                )
            } else {
                ContentUnavailableView(
                    "No Access Request",
                    systemImage: "folder.badge.questionmark",
                    description: Text("There is no pending capability grant, or access was already allowed."),
                )
            }
        }
        .onChange(of: model.grantPromptPresented) { _, presented in
            if !presented {
                dismiss()
            }
        }
    }
}