import AVFoundation
import SwiftUI

struct ContentView: View {
    @State private var apiKey: String = SharedConfig.groqAPIKey ?? ""
    @State private var backend: SharedConfig.Backend = SharedConfig.backend
    @State private var cleanupEnabled: Bool = SharedConfig.cleanupEnabled
    @State private var snippetsEnabled: Bool = SharedConfig.snippetsEnabled
    @State private var micStatus: AVAudioSession.RecordPermission = AVAudioSession.sharedInstance().recordPermission
    @State private var downloadState: DownloadState = .idle
    @State private var showSetupGuide: Bool = false

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Button {
                        showSetupGuide = true
                    } label: {
                        Label("How to install the keyboard", systemImage: "keyboard.badge.eye")
                    }
                } header: {
                    Text("First time?")
                } footer: {
                    Text("Echo Flow runs as a custom keyboard. You need to add it once in iOS Settings before it shows up in the globe key.")
                }

                Section("Microphone") {
                    HStack {
                        Image(systemName: micStatusIcon)
                            .foregroundStyle(micStatusColor)
                        Text(micStatusLabel)
                        Spacer()
                        if micStatus != .granted {
                            Button("Allow") { requestMic() }
                        }
                    }
                }

                Section {
                    SecureField("gsk_...", text: $apiKey)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .onSubmit { SharedConfig.groqAPIKey = apiKey }
                    Button("Save key") {
                        SharedConfig.groqAPIKey = apiKey.trimmingCharacters(in: .whitespacesAndNewlines)
                    }
                    .disabled(apiKey == (SharedConfig.groqAPIKey ?? ""))
                } header: {
                    Text("Groq API key")
                } footer: {
                    Text("Free at console.groq.com — no credit card. The key is stored in a shared app group so the keyboard can read it.")
                }

                Section("Transcription backend") {
                    Picker("Backend", selection: $backend) {
                        ForEach(SharedConfig.Backend.allCases) { b in
                            Text(b.label).tag(b)
                        }
                    }
                    .pickerStyle(.inline)
                    .onChange(of: backend) { _, new in SharedConfig.backend = new }
                }

                Section("On-device model") {
                    HStack {
                        Text(SharedConfig.localWhisperModel)
                            .font(.system(.body, design: .monospaced))
                            .lineLimit(1)
                            .truncationMode(.middle)
                        Spacer()
                        switch downloadState {
                        case .idle:
                            Button("Download") { Task { await prefetchModel() } }
                        case .working:
                            ProgressView().controlSize(.small)
                        case .ready:
                            Label("Ready", systemImage: "checkmark.circle.fill")
                                .foregroundStyle(.green)
                        case .failed(let msg):
                            Label(msg, systemImage: "exclamationmark.triangle.fill")
                                .foregroundStyle(.orange)
                                .font(.caption)
                        }
                    }
                }

                Section("Cleanup") {
                    Toggle("LLM polish (Groq)", isOn: $cleanupEnabled)
                        .onChange(of: cleanupEnabled) { _, new in SharedConfig.cleanupEnabled = new }
                    Toggle("Snippet expansion", isOn: $snippetsEnabled)
                        .onChange(of: snippetsEnabled) { _, new in SharedConfig.snippetsEnabled = new }
                }

                Section {
                    Link("Echo Flow on GitHub", destination: URL(string: "https://github.com/johnsonkc201/echo_flow")!)
                } footer: {
                    Text("v\(Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "?")")
                }
            }
            .navigationTitle("Echo Flow")
            .sheet(isPresented: $showSetupGuide) {
                KeyboardSetupGuideView()
            }
        }
        .onAppear { micStatus = AVAudioSession.sharedInstance().recordPermission }
    }

    private var micStatusIcon: String {
        switch micStatus {
        case .granted: return "mic.fill"
        case .denied: return "mic.slash.fill"
        case .undetermined: return "mic"
        @unknown default: return "mic"
        }
    }

    private var micStatusColor: Color {
        switch micStatus {
        case .granted: return .green
        case .denied: return .red
        default: return .secondary
        }
    }

    private var micStatusLabel: String {
        switch micStatus {
        case .granted: return "Granted"
        case .denied: return "Denied — enable in Settings"
        case .undetermined: return "Not requested yet"
        @unknown default: return "Unknown"
        }
    }

    private func requestMic() {
        AVAudioSession.sharedInstance().requestRecordPermission { _ in
            DispatchQueue.main.async {
                micStatus = AVAudioSession.sharedInstance().recordPermission
            }
        }
    }

    private enum DownloadState {
        case idle
        case working
        case ready
        case failed(String)
    }

    private func prefetchModel() async {
        downloadState = .working
        do {
            try await WhisperLocalTranscriber.shared.warmUp()
            downloadState = .ready
        } catch {
            downloadState = .failed(error.localizedDescription)
        }
    }
}

#Preview {
    ContentView()
}
