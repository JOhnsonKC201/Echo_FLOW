import AVFoundation
import SwiftUI

final class KeyboardBridge: ObservableObject {
    var advanceToNextInputAction: (() -> Void)?
    var insertTextAction: ((String) -> Void)?
    var deleteBackwardAction: (() -> Void)?
    var insertNewlineAction: (() -> Void)?
    var hasFullAccess: Bool = false
}

struct KeyboardView: View {
    @ObservedObject var bridge: KeyboardBridge
    @StateObject private var model = DictationModel()

    var body: some View {
        VStack(spacing: 8) {
            statusRow
            mainRow
            bottomRow
        }
        .padding(.horizontal, 6)
        .padding(.vertical, 8)
        .frame(maxWidth: .infinity)
        .background(Color(uiColor: .systemGray6))
        .onAppear {
            model.onInsert = { bridge.insertTextAction?($0) }
        }
    }

    private var statusRow: some View {
        HStack {
            statusBadge
            Spacer()
            if !bridge.hasFullAccess {
                Label("Enable Full Access in Settings", systemImage: "exclamationmark.triangle.fill")
                    .font(.caption)
                    .foregroundStyle(.orange)
            }
        }
        .frame(height: 18)
        .padding(.horizontal, 4)
    }

    @ViewBuilder
    private var statusBadge: some View {
        switch model.state {
        case .idle:
            Text("Hold to dictate")
                .font(.caption)
                .foregroundStyle(.secondary)
        case .recording(let seconds):
            HStack(spacing: 6) {
                Circle().fill(Color.red).frame(width: 8, height: 8)
                Text(String(format: "Recording %.1fs", seconds))
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.primary)
            }
        case .transcribing:
            HStack(spacing: 6) {
                ProgressView().controlSize(.mini)
                Text("Transcribing…").font(.caption).foregroundStyle(.secondary)
            }
        case .cleaning:
            HStack(spacing: 6) {
                ProgressView().controlSize(.mini)
                Text("Polishing…").font(.caption).foregroundStyle(.secondary)
            }
        case .error(let message):
            Label(message, systemImage: "xmark.circle.fill")
                .font(.caption)
                .foregroundStyle(.red)
                .lineLimit(1)
                .truncationMode(.middle)
        }
    }

    private var mainRow: some View {
        HStack(spacing: 8) {
            keyboardButton(systemImage: "delete.left") {
                bridge.deleteBackwardAction?()
            }
            .frame(width: 56)

            micButton
                .frame(maxWidth: .infinity)

            keyboardButton(systemImage: "return") {
                bridge.insertNewlineAction?()
            }
            .frame(width: 56)
        }
        .frame(height: 80)
    }

    private var bottomRow: some View {
        HStack(spacing: 8) {
            keyboardButton(systemImage: "globe") {
                bridge.advanceToNextInputAction?()
            }
            .frame(width: 56, height: 40)

            spaceKey
                .frame(maxWidth: .infinity, minHeight: 40)
        }
    }

    private var spaceKey: some View {
        Button {
            bridge.insertTextAction?(" ")
        } label: {
            Text("space")
                .font(.callout)
                .foregroundStyle(.primary)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(RoundedRectangle(cornerRadius: 8).fill(Color(uiColor: .systemBackground)))
        }
        .buttonStyle(.plain)
    }

    private func keyboardButton(systemImage: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: systemImage)
                .font(.system(size: 18, weight: .medium))
                .foregroundStyle(.primary)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(RoundedRectangle(cornerRadius: 8).fill(Color(uiColor: .systemBackground)))
        }
        .buttonStyle(.plain)
    }

    private var micButton: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 12)
                .fill(model.isRecording ? Color.red.opacity(0.9) : Color.accentColor)
            HStack(spacing: 10) {
                Image(systemName: model.isRecording ? "waveform" : "mic.fill")
                    .font(.system(size: 22, weight: .semibold))
                Text(model.isRecording ? "Release to send" : "Hold to talk")
                    .font(.callout.weight(.semibold))
            }
            .foregroundStyle(.white)
        }
        .contentShape(RoundedRectangle(cornerRadius: 12))
        .gesture(
            DragGesture(minimumDistance: 0)
                .onChanged { _ in model.beginIfNeeded() }
                .onEnded { value in
                    if value.translation.height < -60 {
                        model.cancel()
                    } else {
                        model.finish()
                    }
                }
        )
        .overlay(alignment: .top) {
            if model.isRecording {
                Text("Slide up to cancel")
                    .font(.caption2)
                    .foregroundStyle(.white.opacity(0.85))
                    .padding(.top, 4)
            }
        }
    }
}

@MainActor
final class DictationModel: ObservableObject {
    enum State: Equatable {
        case idle
        case recording(seconds: Double)
        case transcribing
        case cleaning
        case error(String)
    }

    @Published private(set) var state: State = .idle
    var onInsert: ((String) -> Void)?

    var isRecording: Bool {
        if case .recording = state { return true }
        return false
    }

    private let recorder = AudioRecorder()
    private var transcriber: Transcriber?
    private var timerTask: Task<Void, Never>?
    private var startedAt: Date?

    func beginIfNeeded() {
        guard case .idle = state else { return }
        guard AVAudioSession.sharedInstance().recordPermission == .granted else {
            state = .error("Mic permission denied — open Echo Flow app")
            scheduleClearError()
            return
        }
        do {
            try recorder.startRecording()
            startedAt = Date()
            state = .recording(seconds: 0)
            tickTimer()
        } catch {
            state = .error("Couldn't start recording")
            scheduleClearError()
        }
    }

    func cancel() {
        timerTask?.cancel()
        recorder.cancel()
        state = .idle
    }

    func finish() {
        timerTask?.cancel()
        guard case .recording = state else { return }
        do {
            let url = try recorder.stopRecording()
            guard let started = startedAt, Date().timeIntervalSince(started) > 0.25 else {
                state = .idle
                try? FileManager.default.removeItem(at: url)
                return
            }
            state = .transcribing
            Task { await runPipeline(url: url) }
        } catch {
            state = .error("Recording failed")
            scheduleClearError()
        }
    }

    private func runPipeline(url: URL) async {
        let transcriber = self.transcriber ?? TranscriberFactory.make()
        self.transcriber = transcriber
        do {
            let result = try await transcriber.transcribe(audio: url)
            try? FileManager.default.removeItem(at: url)
            if result.text.isEmpty {
                state = .idle
                return
            }
            state = .cleaning
            let polished = await Cleanup.apply(result.text)
            onInsert?(polished + " ")
            state = .idle
        } catch {
            try? FileManager.default.removeItem(at: url)
            state = .error(error.localizedDescription)
            scheduleClearError()
        }
    }

    private func tickTimer() {
        timerTask?.cancel()
        timerTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 100_000_000)
                guard let self, let started = self.startedAt else { return }
                let elapsed = Date().timeIntervalSince(started)
                if case .recording = self.state {
                    self.state = .recording(seconds: elapsed)
                }
                if elapsed > 60 {
                    self.finish()
                    return
                }
            }
        }
    }

    private func scheduleClearError() {
        Task {
            try? await Task.sleep(nanoseconds: 2_500_000_000)
            if case .error = self.state { self.state = .idle }
        }
    }
}
