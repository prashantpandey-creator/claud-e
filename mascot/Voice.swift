// Voice.swift — Casper's ear and mouth.
//
// Two rules shaped this file:
//
//   1. The animation must follow the REAL signal, not a stand-in for it. The
//      mouth is driven by the amplitude of the audio actually being played,
//      and the listening pose is driven by the loudness actually arriving at
//      the microphone. Nothing here animates on a timer pretending to be
//      speech.
//   2. He must never hear himself. Recognition is muted for the whole time he
//      is speaking, or the first thing he answers is his own last sentence.
//
// Recognition is on-device. That is not a preference — the whole tool's
// promise is that your work never leaves your machine, and a companion that
// ships your voice to a server would break it in the most personal way.

import AVFoundation
import Cocoa
import Speech

// MARK: - the ear

final class Ear: NSObject {
    private let engine = AVAudioEngine()
    private let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?

    /// Smoothed microphone loudness, 0..1. The listening animation reads this.
    private(set) var level: CGFloat = 0
    private(set) var running = false
    private(set) var lastError = ""

    /// True while Casper speaks. Buffers are dropped rather than the engine
    /// torn down, so the tap keeps running and restart is instant.
    var muted = false

    /// Called with in-progress text, so the bubble can show him hearing you.
    var onPartial: ((String) -> Void)?
    /// Called once when you stop talking, with the finished utterance.
    var onUtterance: ((String) -> Void)?

    private var partial = ""
    private var lastLoudAt = Date()
    private var silenceTimer: Timer?
    private let endOfTurnGap: TimeInterval = 1.1 // quiet this long = your turn ended

    /// The room's own noise, learned continuously. A FIXED threshold was the
    /// bug: 0.055 was picked by eye, and this room's ambient measured
    /// 0.033-0.060 — so silence itself kept counting as speech, lastLoudAt
    /// never went stale, and he could never tell you had stopped talking.
    private(set) var noiseFloor: CGFloat = 0.02
    /// Seconds since he last heard something above the floor. Readable so the
    /// end-of-turn decision can be watched from outside instead of guessed at.
    var quietFor: TimeInterval { Date().timeIntervalSince(lastLoudAt) }
    var heardSoFar: String { partial }

    /// Ask for both consents up front. Returns on the main queue.
    static func requestAccess(_ done: @escaping (Bool, String) -> Void) {
        SFSpeechRecognizer.requestAuthorization { speechAuth in
            AVCaptureDevice.requestAccess(for: .audio) { micOK in
                DispatchQueue.main.async {
                    if speechAuth != .authorized {
                        done(false, "speech recognition not authorised (\(speechAuth.rawValue))")
                    } else if !micOK {
                        done(false, "microphone not authorised")
                    } else {
                        done(true, "")
                    }
                }
            }
        }
    }

    func start() {
        guard !running else { return }
        guard let rec = recognizer, rec.isAvailable else {
            lastError = "recogniser unavailable"; return
        }
        guard rec.supportsOnDeviceRecognition else {
            // Stay deaf rather than quietly uploading the owner's voice.
            lastError = "on-device recognition unavailable — staying deaf"
            return
        }
        beginTurn(rec)

        let input = engine.inputNode
        let fmt = input.outputFormat(forBus: 0)
        input.removeTap(onBus: 0)
        input.installTap(onBus: 0, bufferSize: 1024, format: fmt) { [weak self] buf, _ in
            guard let self = self else { return }
            let rms = Ear.rms(buf)
            DispatchQueue.main.async {
                // fast attack, slow release — reads as a voice, not a meter
                let k: CGFloat = rms > self.level ? 0.55 : 0.12
                self.level += (rms - self.level) * k

                // Learn the room: fall to quiet quickly, rise very slowly, so
                // a passing voice cannot drag the floor up and deafen him.
                let fk: CGFloat = rms < self.noiseFloor ? 0.20 : 0.0008
                self.noiseFloor += (rms - self.noiseFloor) * fk

                let speechAt = max(0.018, self.noiseFloor * 2.2 + 0.012)
                if rms > speechAt { self.lastLoudAt = Date() }
            }
            guard !self.muted else { return }
            self.request?.append(buf)
        }

        engine.prepare()
        do { try engine.start() } catch {
            lastError = "audio engine: \(error)"; return
        }
        running = true

        silenceTimer = Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) {
            [weak self] _ in self?.checkEndOfTurn()
        }
    }

    /// A fresh recognition task, i.e. a fresh transcript.
    ///
    /// One long-lived task was the second bug: bestTranscription is CUMULATIVE
    /// for the life of a task, so after the first utterance the same words came
    /// straight back on the next callback, `partial` refilled itself, and no
    /// second thing you said was ever processed as new.
    private func beginTurn(_ rec: SFSpeechRecognizer) {
        task?.cancel()
        request?.endAudio()
        let req = SFSpeechAudioBufferRecognitionRequest()
        req.shouldReportPartialResults = true
        req.requiresOnDeviceRecognition = true
        request = req
        partial = ""
        task = rec.recognitionTask(with: req) { [weak self] result, error in
            guard let self = self else { return }
            if let r = result {
                self.partial = r.bestTranscription.formattedString
                self.onPartial?(self.partial)
            }
            if let e = error as NSError?, e.code != 301 {   // 301 = we cancelled
                self.lastError = "\(e.localizedDescription)"
            }
        }
    }

    /// A turn ends when YOU go quiet, not when the recogniser decides. Waiting
    /// for isFinal makes him feel deaf for seconds after you stop.
    private func checkEndOfTurn() {
        guard running, !muted else { return }
        let text = partial.trimmingCharacters(in: .whitespacesAndNewlines)
        guard text.count >= 2 else { return }
        guard quietFor > endOfTurnGap else { return }
        partial = ""
        if let rec = recognizer { beginTurn(rec) }      // fresh transcript next turn
        onUtterance?(text)
    }

    func stop() {
        silenceTimer?.invalidate(); silenceTimer = nil
        engine.inputNode.removeTap(onBus: 0)
        if engine.isRunning { engine.stop() }
        request?.endAudio(); request = nil
        task?.cancel(); task = nil
        partial = ""
        level = 0
        running = false
    }

    private static func rms(_ buf: AVAudioPCMBuffer) -> CGFloat {
        guard let ch = buf.floatChannelData?[0] else { return 0 }
        let n = Int(buf.frameLength)
        guard n > 0 else { return 0 }
        var sum: Float = 0
        for i in 0..<n { let v = ch[i]; sum += v * v }
        let r = sqrt(sum / Float(n))
        // speech sits low in linear terms; lift it into a usable 0..1
        return CGFloat(min(1, max(0, r * 12)))
    }
}

// MARK: - the mouth

/// Speaks, and reports the amplitude of what is audible RIGHT NOW.
///
/// The text is rendered to PCM first, then played through our own engine, so
/// `drive` is measured from the same samples reaching the speakers. A mouth
/// animated off a timer drifts away from the voice within a sentence; this
/// one cannot, because it is the voice.
final class Mouth: NSObject, AVSpeechSynthesizerDelegate {
    private let synth = AVSpeechSynthesizer()
    private let engine = AVAudioEngine()
    private let player = AVAudioPlayerNode()

    /// 0..1 amplitude of the audio playing this instant.
    private(set) var drive: CGFloat = 0
    private(set) var speaking = false
    var onStart: (() -> Void)?
    var onFinish: (() -> Void)?

    private var attached = false

    override init() {
        super.init()
        synth.delegate = self
    }

    func say(_ text: String) {
        let clean = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !clean.isEmpty else { return }
        let u = AVSpeechUtterance(string: clean)
        u.rate = 0.52
        u.pitchMultiplier = 1.06          // a shade brighter than default
        u.voice = Mouth.bestVoice()

        var chunks: [AVAudioPCMBuffer] = []
        synth.write(u) { [weak self] buffer in
            guard let self = self else { return }
            guard let pcm = buffer as? AVAudioPCMBuffer, pcm.frameLength > 0 else {
                // zero-length buffer marks the end of the render
                if !chunks.isEmpty { self.play(chunks) ; chunks = [] }
                return
            }
            chunks.append(pcm)
        }
        // Some macOS builds finish the render synchronously and never send a
        // terminating empty buffer; flush whatever arrived.
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) { [weak self] in
            guard let self = self, !chunks.isEmpty, !self.speaking else { return }
            self.play(chunks); chunks = []
        }
    }

    private func play(_ chunks: [AVAudioPCMBuffer]) {
        guard let first = chunks.first else { return }
        guard let joined = Mouth.concat(chunks, format: first.format) else { return }

        if !attached {
            engine.attach(player)
            engine.connect(player, to: engine.mainMixerNode, format: joined.format)
            engine.mainMixerNode.removeTap(onBus: 0)
            engine.mainMixerNode.installTap(onBus: 0, bufferSize: 1024,
                                            format: nil) { [weak self] buf, _ in
                guard let self = self else { return }
                let a = Mouth.amplitude(buf)
                DispatchQueue.main.async {
                    let k: CGFloat = a > self.drive ? 0.6 : 0.42
                    self.drive += (a - self.drive) * k
                }
            }
            attached = true
        }
        do {
            if !engine.isRunning { engine.prepare(); try engine.start() }
        } catch { return }

        speaking = true
        onStart?()
        player.scheduleBuffer(joined, at: nil, options: []) { [weak self] in
            DispatchQueue.main.async {
                guard let self = self else { return }
                self.speaking = false
                self.drive = 0
                self.onFinish?()
            }
        }
        player.play()
    }

    func shutUp() {
        player.stop()
        synth.stopSpeaking(at: .immediate)
        speaking = false
        drive = 0
    }

    private static func concat(_ bufs: [AVAudioPCMBuffer],
                               format: AVAudioFormat) -> AVAudioPCMBuffer? {
        let total = bufs.reduce(0) { $0 + $1.frameLength }
        guard total > 0,
              let out = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: total)
        else { return nil }
        var at: AVAudioFrameCount = 0
        let channels = Int(format.channelCount)
        for b in bufs {
            let n = Int(b.frameLength)
            if let src = b.floatChannelData, let dst = out.floatChannelData {
                for c in 0..<channels {
                    memcpy(dst[c] + Int(at), src[c], n * MemoryLayout<Float>.size)
                }
            } else if let src = b.int16ChannelData, let dst = out.int16ChannelData {
                for c in 0..<channels {
                    memcpy(dst[c] + Int(at), src[c], n * MemoryLayout<Int16>.size)
                }
            }
            at += b.frameLength
        }
        out.frameLength = total
        return out
    }

    private static func amplitude(_ buf: AVAudioPCMBuffer) -> CGFloat {
        guard let ch = buf.floatChannelData?[0] else { return 0 }
        let n = Int(buf.frameLength)
        guard n > 0 else { return 0 }
        // RMS, not peak. Peak saturated at 1.0 for almost every buffer, which
        // measured as "the envelope moves" while actually holding his mouth
        // wide open through the whole sentence. RMS tracks how loud the
        // syllable really is, so the mouth closes between words.
        var sum: Float = 0
        for i in 0..<n { let v = ch[i]; sum += v * v }
        let r = sqrt(sum / Float(n))
        // Expand the dynamics. Plain linear gain forces a choice between a
        // mouth that never opens fully and one that never closes: measured
        // mean/peak was 0.73 either way, i.e. speech is nearly continuous in
        // RMS. The power curve keeps loud syllables at full open while
        // pushing the quiet between words down to a closed mouth.
        let norm = min(1.0, max(0.0, Double(r) * 5.0))
        return CGFloat(pow(norm, 2.6))
    }

    /// Prefer a natural voice if the machine has one downloaded — the compact
    /// voices are the ones that make a companion sound like a phone menu.
    static func bestVoice() -> AVSpeechSynthesisVoice? {
        let all = AVSpeechSynthesisVoice.speechVoices().filter {
            $0.language.hasPrefix("en")
        }
        let byQuality = all.sorted { a, b in a.quality.rawValue > b.quality.rawValue }
        let wanted = ["Ava", "Zoe", "Evan", "Samantha", "Serena"]
        for name in wanted {
            if let v = byQuality.first(where: { $0.name.contains(name) }) { return v }
        }
        return byQuality.first ?? AVSpeechSynthesisVoice(language: "en-US")
    }
}
