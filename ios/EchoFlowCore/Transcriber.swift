import Foundation

public struct TranscriptionResult {
    public let text: String
    public let source: String
    public init(text: String, source: String) {
        self.text = text
        self.source = source
    }
}

public protocol Transcriber {
    func transcribe(audio url: URL) async throws -> TranscriptionResult
}

public enum TranscriberError: Error, LocalizedError {
    case noBackendAvailable
    case allBackendsFailed(primary: Error, fallback: Error?)

    public var errorDescription: String? {
        switch self {
        case .noBackendAvailable:
            return "No transcription backend configured. Open the Echo Flow app and add a Groq API key, or enable on-device Whisper."
        case .allBackendsFailed(let primary, let fallback):
            if let fallback = fallback {
                return "Both transcription backends failed. Cloud: \(primary.localizedDescription). On-device: \(fallback.localizedDescription)."
            }
            return "Transcription failed: \(primary.localizedDescription)"
        }
    }
}

public final class HybridTranscriber: Transcriber {
    private let primary: Transcriber?
    private let fallback: Transcriber?

    public init(primary: Transcriber?, fallback: Transcriber?) {
        self.primary = primary
        self.fallback = fallback
    }

    public func transcribe(audio url: URL) async throws -> TranscriptionResult {
        if let primary = primary {
            do {
                return try await primary.transcribe(audio: url)
            } catch {
                guard let fallback = fallback else {
                    throw TranscriberError.allBackendsFailed(primary: error, fallback: nil)
                }
                do {
                    return try await fallback.transcribe(audio: url)
                } catch let fallbackError {
                    throw TranscriberError.allBackendsFailed(primary: error, fallback: fallbackError)
                }
            }
        }
        if let fallback = fallback {
            return try await fallback.transcribe(audio: url)
        }
        throw TranscriberError.noBackendAvailable
    }
}

public enum TranscriberFactory {
    public static func make() -> Transcriber {
        let groq: Transcriber? = SharedConfig.groqAPIKey.map { GroqTranscriber(apiKey: $0) }
        let local: Transcriber? = WhisperLocalTranscriber.shared

        switch SharedConfig.backend {
        case .groqWithFallback:
            return HybridTranscriber(primary: groq, fallback: local)
        case .groqOnly:
            return HybridTranscriber(primary: groq, fallback: nil)
        case .localOnly:
            return HybridTranscriber(primary: local, fallback: nil)
        }
    }
}
