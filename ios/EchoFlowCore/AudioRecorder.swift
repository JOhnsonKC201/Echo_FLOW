import AVFoundation
import Foundation

public enum AudioRecorderError: Error {
    case sessionConfiguration(Error)
    case recordStartFailed
    case noRecording
}

public final class AudioRecorder {
    private var recorder: AVAudioRecorder?
    private(set) public var currentURL: URL?

    public init() {}

    public func startRecording() throws {
        let session = AVAudioSession.sharedInstance()
        do {
            try session.setCategory(.playAndRecord, mode: .spokenAudio, options: [.defaultToSpeaker, .allowBluetooth])
            try session.setActive(true, options: [])
        } catch {
            throw AudioRecorderError.sessionConfiguration(error)
        }

        let tmp = FileManager.default.temporaryDirectory
            .appendingPathComponent("echoflow-\(UUID().uuidString)")
            .appendingPathExtension("m4a")

        let settings: [String: Any] = [
            AVFormatIDKey: kAudioFormatMPEG4AAC,
            AVSampleRateKey: 16_000,
            AVNumberOfChannelsKey: 1,
            AVEncoderBitRateKey: 32_000,
            AVEncoderAudioQualityKey: AVAudioQuality.medium.rawValue,
        ]

        let recorder = try AVAudioRecorder(url: tmp, settings: settings)
        recorder.isMeteringEnabled = true
        guard recorder.record() else {
            throw AudioRecorderError.recordStartFailed
        }
        self.recorder = recorder
        self.currentURL = tmp
    }

    @discardableResult
    public func stopRecording() throws -> URL {
        guard let recorder = recorder, let url = currentURL else {
            throw AudioRecorderError.noRecording
        }
        recorder.stop()
        self.recorder = nil
        try? AVAudioSession.sharedInstance().setActive(false, options: [.notifyOthersOnDeactivation])
        return url
    }

    public func cancel() {
        recorder?.stop()
        if let url = currentURL {
            try? FileManager.default.removeItem(at: url)
        }
        recorder = nil
        currentURL = nil
        try? AVAudioSession.sharedInstance().setActive(false, options: [.notifyOthersOnDeactivation])
    }

    public var durationSeconds: TimeInterval {
        recorder?.currentTime ?? 0
    }

    public var averagePower: Float {
        guard let r = recorder else { return -160 }
        r.updateMeters()
        return r.averagePower(forChannel: 0)
    }
}
