import SwiftUI

struct CommandPaletteView: View {
    @EnvironmentObject private var model: CapDepAppModel
    @Environment(\.dismiss) private var dismiss
    @Environment(\.openWindow) private var openWindow
    @FocusState private var inputFocused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Ask CapDep")
                        .font(.title2.weight(.semibold))
                    Text(model.contextChips.isEmpty ? "No app context attached yet." : "\(model.contextChips.count) context item\(model.contextChips.count == 1 ? "" : "s") ready")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button {
                    Task { await model.refreshFrontmostContext() }
                } label: {
                    Label("Current App", systemImage: "scope")
                }
                .controlSize(.small)
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
                    .font(.body)
                    .lineLimit(2...4)
                    .padding(12)
                    .background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 8))
                    .focused($inputFocused)
                    .onSubmit {
                        Task {
                            await model.submitCommand()
                            openWindow(id: "main")
                            dismiss()
                        }
                    }

                ContextChipRow(chips: model.contextChips) { chip in
                    model.removeContextChip(chip)
                }
            }

            VStack(alignment: .leading, spacing: 10) {
                Text("Suggested")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 190), spacing: 10)], spacing: 10) {
                    ForEach(model.workflows.prefix(6)) { workflow in
                        Button {
                            Task {
                                await model.launchWorkflow(workflow)
                                openWindow(id: "main")
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
                Button("Open Chat") {
                    openWindow(id: "main")
                }
                Button("Submit") {
                    Task {
                        await model.submitCommand()
                        openWindow(id: "main")
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
            if model.contextChips.isEmpty {
                Task { await model.refreshFrontmostContext() }
            }
        }
    }
}

struct ContextChipRow: View {
    let chips: [ContextChip]
    var onRemove: ((ContextChip) -> Void)? = nil

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(chips) { chip in
                    HStack(spacing: 6) {
                        Image(systemName: chip.isUntrusted ? "exclamationmark.triangle" : "paperclip")
                        Text(chip.title)
                        Text(chip.detail)
                            .foregroundStyle(.secondary)
                        if let onRemove {
                            Button {
                                onRemove(chip)
                            } label: {
                                Image(systemName: "xmark.circle.fill")
                            }
                            .buttonStyle(.plain)
                        }
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
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Image(systemName: workflow.systemImage)
                    .font(.headline)
                Spacer()
                if workflow.requiresForegroundReview {
                    Image(systemName: "hand.raised")
                        .foregroundStyle(.yellow)
                }
            }
            Text(workflow.title)
                .font(.subheadline.weight(.semibold))
            Text(workflow.subtitle)
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(2)
            Text(workflow.purpose.rawValue.capitalized)
                .font(.caption2.weight(.semibold))
                .padding(.horizontal, 8)
                .padding(.vertical, 4)
                .background(.quaternary, in: Capsule())
        }
        .padding(12)
        .frame(maxWidth: .infinity, minHeight: 116, alignment: .leading)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 8))
        .overlay(
            RoundedRectangle(cornerRadius: 8)
                .stroke(.quaternary),
        )
    }
}
