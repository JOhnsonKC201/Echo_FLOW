import SwiftUI
import UIKit

struct KeyboardSetupGuideView: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                Section("One time, in iOS Settings") {
                    step(1, "Open Settings, then General, then Keyboard, then Keyboards.")
                    step(2, "Tap “Add New Keyboard…” and pick Echo Flow.")
                    step(3, "Tap the new Echo Flow row and turn on “Allow Full Access”. This is required for the microphone and network.")
                }

                Section("Every time you want to dictate") {
                    step(1, "Open any text field.")
                    step(2, "Long-press the globe (🌐) key, choose Echo Flow.")
                    step(3, "Hold the mic button, speak, release. The transcript inserts where your cursor is.")
                }

                Section {
                    Link(destination: URL(string: UIApplication.openSettingsURLString)!) {
                        Label("Open Echo Flow in Settings", systemImage: "gearshape")
                    }
                } footer: {
                    Text("Allow Full Access enables network calls (Groq) and microphone access. Echo Flow stores nothing outside the app group on your device.")
                }
            }
            .navigationTitle("Install the keyboard")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }

    private func step(_ n: Int, _ text: String) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Text("\(n)")
                .font(.headline.monospacedDigit())
                .frame(width: 24, height: 24)
                .background(Circle().fill(Color.accentColor.opacity(0.15)))
                .foregroundStyle(Color.accentColor)
            Text(text)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.vertical, 2)
    }
}

#Preview {
    KeyboardSetupGuideView()
}
