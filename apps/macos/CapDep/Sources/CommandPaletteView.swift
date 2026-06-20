import SwiftUI

struct CommandPaletteView: View {
    @EnvironmentObject private var model: CapDepAppModel
    @Environment(\.dismiss) private var dismiss
    @Environment(\.openWindow) private var openWindow
    @FocusState private var inputFocused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Ask CapDep")
                        .font(.largeTitle.weight(.bold))
                    Text("Attach context deliberately. The daemon decides what is allowed.")
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Picker("Purpose", selection: $model.selectedPurpose) {
                    ForEach(Purpose.allCases) { purpose in
                        Text(purpose.rawValue.capitalized).tag(purpose)
                    }
                }
                .pickerStyle(.menu)
                .frame(width: 150)
            }

            VStack(alignment: .leading, spacing: 10) {
                TextField("Ask about the current app, selected files, inbox, calendar, or web research...", text: $model.commandText, axis: .vertical)
                    .textFieldStyle(.plain)
                    .font(.title3)
                    .lineLimit(3...5)
                    .padding(14)
                    .background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 16))
                    .focused($inputFocused)
                    .onSubmit {
                        Task {
                            await model.submitCommand()
                            openWindow(id: "task-panel")
                            dismiss()
                        }
                    }

                ContextChipRow(chips: model.contextChips)
            }

            VStack(alignment: .leading, spacing: 10) {
                Text("Suggested Workflows")
                    .font(.headline)
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 210), spacing: 12)], spacing: 12) {
                    ForEach(model.workflows) { workflow in
                        Button {
                            Task {
                                await model.launchWorkflow(workflow)
                                openWindow(id: "task-panel")
                                dismiss()
                            }
                        } label: {
                            WorkflowTile(workflow: workflow)
                        }
                        .buttonStyle(.plain)
                    }
                }
            }

            Spacer()

            HStack {
                Label(model.connected ? "Daemon connected" : "Daemon offline", systemImage: model.connected ? "checkmark.circle" : "xmark.octagon")
                    .foregroundStyle(model.connected ? .green : .red)
                Spacer()
                Button("Open Dashboard") {
                    openWindow(id: "main")
                }
                Button("Submit") {
                    Task {
                        await model.submitCommand()
                        openWindow(id: "task-panel")
                        dismiss()
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(model.commandText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(24)
        .onAppear {
            inputFocused = true
        }
    }
}

struct ContextChipRow: View {
    let chips: [ContextChip]

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(chips) { chip in
                    HStack(spacing: 6) {
                        Image(systemName: chip.isUntrusted ? "exclamationmark.triangle" : "paperclip")
                        Text(chip.title)
                        Text(chip.detail)
                            .foregroundStyle(.secondary)
                    }
                    .font(.caption)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 6)
                    .background(chipColor(chip), in: Capsule())
                }
            }
        }
    }

    private func chipColor(_ chip: ContextChip) -> Color {
        if chip.isUntrusted {
            return .yellow.opacity(0.20)
        }
        if chip.isSensitive {
            return .red.opacity(0.14)
        }
        return .blue.opacity(0.12)
    }
}

struct WorkflowTile: View {
    let workflow: WorkflowTemplate

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Image(systemName: workflow.systemImage)
                    .font(.title2)
                Spacer()
                if workflow.requiresForegroundReview {
                    Image(systemName: "hand.raised")
                        .foregroundStyle(.yellow)
                }
            }
            Text(workflow.title)
                .font(.headline)
            Text(workflow.subtitle)
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(3)
            Text(workflow.purpose.rawValue.capitalized)
                .font(.caption2.weight(.semibold))
                .padding(.horizontal, 8)
                .padding(.vertical, 4)
                .background(.quaternary, in: Capsule())
        }
        .padding()
        .frame(maxWidth: .infinity, minHeight: 138, alignment: .leading)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 16))
        .overlay(
            RoundedRectangle(cornerRadius: 16)
                .stroke(.quaternary),
        )
    }
}
