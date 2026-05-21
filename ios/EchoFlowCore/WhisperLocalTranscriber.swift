import Foundation
import WhisperKit

public final class WhisperLocalTranscriber: Transcriber {
    public static let shared = WhisperLocalTranscriber()

    public enum LocalWhisperError: Error, LocalizedError {
        case modelNotReady
        case transcriptionFailed(Error)

        public var errorDescription: String? {
            switch self {
            case .modelNotReady:
                return "On-device Whisper model isn't ready yet. Open the Echo Flow app while online to download it."
            case .transcriptionFailed(let error):
                return "On-device Whisper failed: \(error.localizedDescription)"
            }
        }
    }

    private var pipe: WhisperKit?
    private let lock = NSLock()

    private init() {}

    public func warmUp() async throws {
        _ = try await pipeline()
    }

    public func transcribe(audio url: URL) async throws -> TranscriptionResult {
        let pipeline = try await pipeline()
        do {
            let results = try await pipeline.transcribe(audioPath: url.path)
            let text = results.map(\.text).joined(separator: " ")
                .trimmingCharacters(in: .whitespacesAndNewlines)
            return TranscriptionResult(text: text, source: "whisperkit")
        } catch {
            throw LocalWhisperError.transcriptionFailed(error)
        }
    }

    private func pipeline() async throws -> WhisperKit {
        lock.lock()
        let existing = pipe
        lock.unlock()
        if let existing = existing { return existing }

        let kit = try await WhisperKit(model: SharedConfig.localWhisperModel)
        lock.lock()
        self.pipe = kit
        lock.unlock()
        return kit
    }
}
