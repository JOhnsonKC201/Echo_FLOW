import Foundation

public enum AppGroup {
    public static let identifier = "group.com.echoflow.shared"

    public static var defaults: UserDefaults {
        UserDefaults(suiteName: identifier) ?? .standard
    }

    public static var containerURL: URL? {
        FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: identifier)
    }
}

public struct SharedConfig {
    public enum Backend: String, CaseIterable, Identifiable {
        case groqWithFallback
        case groqOnly
        case localOnly
        public var id: String { rawValue }
        public var label: String {
            switch self {
            case .groqWithFallback: return "Groq, fall back to on-device"
            case .groqOnly: return "Groq only"
            case .localOnly: return "On-device only"
            }
        }
    }

    private static let groqKeyKey = "groq_api_key"
    private static let backendKey = "transcription_backend"
    private static let cleanupKey = "cleanup_enabled"
    private static let snippetsKey = "snippets_enabled"
    private static let localModelKey = "local_whisper_model"

    public static var groqAPIKey: String? {
        get { AppGroup.defaults.string(forKey: groqKeyKey)?.nonEmpty }
        set { AppGroup.defaults.set(newValue, forKey: groqKeyKey) }
    }

    public static var backend: Backend {
        get {
            guard let raw = AppGroup.defaults.string(forKey: backendKey),
                  let b = Backend(rawValue: raw) else { return .groqWithFallback }
            return b
        }
        set { AppGroup.defaults.set(newValue.rawValue, forKey: backendKey) }
    }

    public static var cleanupEnabled: Bool {
        get { AppGroup.defaults.object(forKey: cleanupKey) as? Bool ?? true }
        set { AppGroup.defaults.set(newValue, forKey: cleanupKey) }
    }

    public static var snippetsEnabled: Bool {
        get { AppGroup.defaults.object(forKey: snippetsKey) as? Bool ?? true }
        set { AppGroup.defaults.set(newValue, forKey: snippetsKey) }
    }

    public static var localWhisperModel: String {
        get { AppGroup.defaults.string(forKey: localModelKey) ?? "openai_whisper-tiny.en" }
        set { AppGroup.defaults.set(newValue, forKey: localModelKey) }
    }
}

public extension SharedConfig {
    static let defaultSnippets: [String: String] = [
        "btw": "by the way",
        "fyi": "for your information",
        "ttyl": "talk to you later",
        "lgtm": "looks good to me",
        "imo": "in my opinion",
        "afaik": "as far as I know",
        "asap": "as soon as possible",
        "eta": "estimated time of arrival",
        "tbh": "to be honest",
        "idk": "I don't know",
    ]
}

private extension String {
    var nonEmpty: String? { isEmpty ? nil : self }
}
