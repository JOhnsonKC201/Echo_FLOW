import Foundation

public final class GroqTranscriber: Transcriber {
    public enum GroqError: Error, LocalizedError {
        case httpError(status: Int, body: String)
        case invalidResponse
        case fileReadFailed
        case missingAPIKey

        public var errorDescription: String? {
            switch self {
            case .httpError(let status, let body):
                return "Groq HTTP \(status): \(body.prefix(200))"
            case .invalidResponse:
                return "Groq returned an unexpected response."
            case .fileReadFailed:
                return "Couldn't read the recorded audio file."
            case .missingAPIKey:
                return "Missing Groq API key."
            }
        }
    }

    private let apiKey: String
    private let model: String
    private let endpoint = URL(string: "https://api.groq.com/openai/v1/audio/transcriptions")!
    private let session: URLSession

    public init(apiKey: String, model: String = "whisper-large-v3-turbo") {
        self.apiKey = apiKey
        self.model = model
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 20
        config.waitsForConnectivity = false
        self.session = URLSession(configuration: config)
    }

    public func transcribe(audio url: URL) async throws -> TranscriptionResult {
        guard !apiKey.isEmpty else { throw GroqError.missingAPIKey }
        guard let audioData = try? Data(contentsOf: url) else {
            throw GroqError.fileReadFailed
        }

        let boundary = "EchoFlow-\(UUID().uuidString)"
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.setValue("Bearer \(apiKey)", forHTTPHeaderField: "Authorization")
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        request.httpBody = Self.multipartBody(
            boundary: boundary,
            audio: audioData,
            filename: url.lastPathComponent,
            model: model,
            language: "en"
        )

        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw GroqError.invalidResponse
        }
        guard (200..<300).contains(http.statusCode) else {
            let body = String(data: data, encoding: .utf8) ?? ""
            throw GroqError.httpError(status: http.statusCode, body: body)
        }

        guard let json = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let text = json["text"] as? String else {
            throw GroqError.invalidResponse
        }
        return TranscriptionResult(text: text.trimmingCharacters(in: .whitespacesAndNewlines), source: "groq")
    }

    private static func multipartBody(
        boundary: String,
        audio: Data,
        filename: String,
        model: String,
        language: String?
    ) -> Data {
        var body = Data()
        let boundaryPrefix = "--\(boundary)\r\n"

        func appendField(name: String, value: String) {
            body.append(boundaryPrefix)
            body.append("Content-Disposition: form-data; name=\"\(name)\"\r\n\r\n")
            body.append("\(value)\r\n")
        }

        appendField(name: "model", value: model)
        appendField(name: "response_format", value: "json")
        appendField(name: "temperature", value: "0")
        if let language = language {
            appendField(name: "language", value: language)
        }

        body.append(boundaryPrefix)
        body.append("Content-Disposition: form-data; name=\"file\"; filename=\"\(filename)\"\r\n")
        body.append("Content-Type: audio/mp4\r\n\r\n")
        body.append(audio)
        body.append("\r\n")
        body.append("--\(boundary)--\r\n")
        return body
    }
}

private extension Data {
    mutating func append(_ string: String) {
        if let data = string.data(using: .utf8) {
            append(data)
        }
    }
}
