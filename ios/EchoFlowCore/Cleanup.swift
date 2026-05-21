import Foundation

public enum Cleanup {
    public static func apply(_ text: String) async -> String {
        var out = text
        if SharedConfig.cleanupEnabled, let apiKey = SharedConfig.groqAPIKey {
            if let polished = try? await polish(out, apiKey: apiKey) {
                out = polished
            }
        }
        if SharedConfig.snippetsEnabled {
            out = expandSnippets(out, snippets: SharedConfig.defaultSnippets)
        }
        return out.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    static func expandSnippets(_ text: String, snippets: [String: String]) -> String {
        guard !snippets.isEmpty else { return text }
        var result = text
        for (code, expansion) in snippets {
            let pattern = "\\b" + NSRegularExpression.escapedPattern(for: code) + "\\b"
            guard let regex = try? NSRegularExpression(pattern: pattern, options: [.caseInsensitive]) else { continue }
            let range = NSRange(result.startIndex..<result.endIndex, in: result)
            let matches = regex.matches(in: result, options: [], range: range).reversed()
            for match in matches {
                guard let r = Range(match.range, in: result) else { continue }
                let original = String(result[r])
                result.replaceSubrange(r, with: matchCase(of: original, in: expansion))
            }
        }
        return result
    }

    private static func matchCase(of original: String, in replacement: String) -> String {
        if original == original.uppercased() { return replacement.uppercased() }
        if let first = original.first, first.isUppercase {
            return replacement.prefix(1).uppercased() + replacement.dropFirst()
        }
        return replacement
    }

    private static let chatEndpoint = URL(string: "https://api.groq.com/openai/v1/chat/completions")!

    private static func polish(_ text: String, apiKey: String) async throws -> String {
        var request = URLRequest(url: chatEndpoint)
        request.httpMethod = "POST"
        request.setValue("Bearer \(apiKey)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")

        let body: [String: Any] = [
            "model": "llama-3.1-8b-instant",
            "temperature": 0,
            "max_tokens": 400,
            "messages": [
                ["role": "system", "content": Self.systemPrompt],
                ["role": "user", "content": text],
            ],
        ]
        request.httpBody = try JSONSerialization.data(withJSONObject: body)

        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 6
        let session = URLSession(configuration: config)

        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else { return text }

        guard let json = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let choices = json["choices"] as? [[String: Any]],
              let message = choices.first?["message"] as? [String: Any],
              let content = message["content"] as? String else { return text }

        let cleaned = content.trimmingCharacters(in: .whitespacesAndNewlines)
        return hallucinationGuard(original: text, cleaned: cleaned) ? cleaned : text
    }

    private static let systemPrompt = """
    You receive raw voice-to-text dictation and return a polished version. Rules:
    - Fix obvious transcription errors and add natural punctuation/capitalization.
    - Preserve the speaker's meaning, tone, and word choice. Do not paraphrase.
    - Do not add greetings, sign-offs, or commentary. Return only the cleaned text.
    - If the input is empty or pure noise, return it unchanged.
    """

    private static func hallucinationGuard(original: String, cleaned: String) -> Bool {
        guard !cleaned.isEmpty else { return false }
        let ratio = Double(cleaned.count) / Double(max(original.count, 1))
        return ratio > 0.5 && ratio < 3.0
    }
}
