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

    /// The names this workspace is made of. A general recogniser has never
    /// heard of them, and measured on this machine it got 4 of 14 words wrong
    /// on a sentence made of them. Loaded once — this shells out to python.
    static let vocabulary: [String] = {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        p.arguments = ["python3",
                       (("~/.claude/skills/meditate/vocabulary.py" as NSString)
                            .expandingTildeInPath)]
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = Pipe()
        do { try p.run() } catch { return [] }
        let d = pipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        return (String(data: d, encoding: .utf8) ?? "")
            .split(separator: "\n").map(String.init)
            .filter { !$0.isEmpty }
    }()

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
        req.contextualStrings = Ear.vocabulary      // your words, not the world's
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

    /// Which lane produced the last utterance — "kokoro" or "apple".
    /// Observable so nobody has to guess whether the good voice is actually
    /// the one talking.
    private(set) var lane = "apple"

    /// Ask the warm Kokoro server (tts.py --serve, loopback :7712) to render.
    /// Returns the samples, or nil in one network timeout when it is down —
    /// the fallback must be instant, not a stall.
    private func kokoroRender(_ text: String) -> AVAudioPCMBuffer? {
        guard let url = URL(string: "http://127.0.0.1:7712/tts") else { return nil }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.timeoutInterval = 12                    // render is ~2s warm
        req.httpBody = try? JSONSerialization.data(withJSONObject: ["text": text])
        var wavPath: String?
        let sem = DispatchSemaphore(value: 0)
        URLSession.shared.dataTask(with: req) { data, _, _ in
            defer { sem.signal() }
            guard let d = data,
                  let j = try? JSONSerialization.jsonObject(with: d) as? [String: Any],
                  j["ok"] as? Bool == true,
                  let p = j["wav"] as? String else { return }
            wavPath = p
        }.resume()
        _ = sem.wait(timeout: .now() + 13)
        guard let p = wavPath else { return nil }
        defer { try? FileManager.default.removeItem(atPath: p) }
        guard let f = try? AVAudioFile(forReading: URL(fileURLWithPath: p)),
              let buf = AVAudioPCMBuffer(pcmFormat: f.processingFormat,
                                         frameCapacity: AVAudioFrameCount(f.length)),
              (try? f.read(into: buf)) != nil, buf.frameLength > 0
        else { return nil }
        // Peak-normalise. Kokoro renders quieter than Apple (measured peak
        // 0.356 vs 0.667 through the same tap), which both drops his volume
        // when the lane switches and half-closes the mouth animation, since
        // the mouth reads the amplitude of what is actually playing.
        if let ch = buf.floatChannelData {
            let n = Int(buf.frameLength)
            var peak: Float = 0
            for c in 0..<Int(buf.format.channelCount) {
                for i in 0..<n { peak = max(peak, abs(ch[c][i])) }
            }
            if peak > 0.01, peak < 0.85 {
                let g = 0.9 / peak
                for c in 0..<Int(buf.format.channelCount) {
                    for i in 0..<n { ch[c][i] *= g }
                }
            }
        }
        return buf
    }

    /// Speak, one sentence at a time, with a real beat between them.
    ///
    /// Pacing is most of what "soothing" is. Said as one unbroken run, even a
    /// good voice sounds like it is reading a warning label; a fifth of a
    /// second between sentences is the difference between a colleague and a
    /// public-address system. The gap is real silence spliced into the audio,
    /// so the mouth closes during it too.
    func say(_ text: String) {
        let clean = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !clean.isEmpty else { return }
        // Kokoro first: rendered off the main thread, whole utterance in one
        // call (it does its own prosody and pauses — our splicing is for the
        // Apple lane). Falls back to Apple in one timeout when the server is
        // down, so he is never mute, just less human for a while.
        DispatchQueue.global().async { [weak self] in
            guard let self = self else { return }
            if let buf = self.kokoroRender(clean) {
                DispatchQueue.main.async {
                    self.lane = "kokoro"
                    self.play([buf])
                }
            } else {
                DispatchQueue.main.async {
                    self.lane = "apple"
                    self.renderNext(Mouth.sentences(clean), 0, [])
                }
            }
        }
    }

    /// Split on sentence ends, keeping the punctuation — the synthesiser uses
    /// it for intonation, and a sentence stripped of its full stop is read
    /// flat.
    static func sentences(_ text: String) -> [String] {
        var out: [String] = []
        var cur = ""
        for ch in text {
            cur.append(ch)
            if ch == "." || ch == "!" || ch == "?" {
                let t = cur.trimmingCharacters(in: .whitespaces)
                if t.count > 1 { out.append(t); cur = "" }
            }
        }
        let tail = cur.trimmingCharacters(in: .whitespaces)
        if !tail.isEmpty { out.append(tail) }
        return out.isEmpty ? [text] : out
    }

    private func utterance(_ s: String) -> AVSpeechUtterance {
        let u = AVSpeechUtterance(string: s)
        // Unhurried, and a touch BELOW default pitch. The old settings ran at
        // 0.52 with pitch 1.06 — faster and brighter, which is the register of
        // a hold message, not of someone thinking about your work.
        u.rate = 0.46
        u.pitchMultiplier = 0.97
        // volume stays at 1.0: how loud he is, is the system volume's job,
        // and turning it down here quietly halved the mouth animation too
        u.voice = Mouth.bestVoice()
        return u
    }

    /// Render sentences in order, splicing silence between them, then play the
    /// whole thing as one buffer so nothing can drift.
    private func renderNext(_ parts: [String], _ i: Int,
                            _ acc: [AVAudioPCMBuffer]) {
        guard i < parts.count else {
            if !acc.isEmpty { play(acc) }
            return
        }
        var chunks: [AVAudioPCMBuffer] = []
        var advanced = false
        let step = { [weak self] in
            guard let self = self, !advanced else { return }
            advanced = true
            var next = acc + chunks
            if i < parts.count - 1, let first = chunks.first,
               let gap = Mouth.silence(seconds: 0.20, like: first) {
                next.append(gap)
            }
            self.renderNext(parts, i + 1, next)
        }
        synth.write(utterance(parts[i])) { buffer in
            guard let pcm = buffer as? AVAudioPCMBuffer, pcm.frameLength > 0 else {
                step(); return              // zero-length buffer ends the render
            }
            chunks.append(pcm)
        }
        // Some macOS builds finish synchronously and never send the terminator.
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.45) { step() }
    }

    /// A buffer of nothing, in the same format as the speech around it.
    static func silence(seconds: Double, like ref: AVAudioPCMBuffer)
        -> AVAudioPCMBuffer? {
        let frames = AVAudioFrameCount(ref.format.sampleRate * seconds)
        guard frames > 0,
              let b = AVAudioPCMBuffer(pcmFormat: ref.format, frameCapacity: frames)
        else { return nil }
        b.frameLength = frames
        let channels = Int(ref.format.channelCount)
        if let d = b.floatChannelData {
            for c in 0..<channels {
                memset(d[c], 0, Int(frames) * MemoryLayout<Float>.size)
            }
        } else if let d = b.int16ChannelData {
            for c in 0..<channels {
                memset(d[c], 0, Int(frames) * MemoryLayout<Int16>.size)
            }
        }
        return b
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

    /// Voices ranked for a calm, professional read.
    ///
    /// The old ranking sorted by quality and then searched that list BY NAME,
    /// which threw the sort away: a compact "Ava" beat a premium anything.
    /// Quality has to win first, because it is the only thing here that
    /// actually changes how synthetic he sounds. Measured on this Mac: all
    /// 180 installed voices are quality tier 1 (compact). That is the ceiling
    /// until enhanced or premium voices are downloaded.
    static let preferenceKey = "casper.voice"

    /// Calm and measured first, bright and chirpy last. Order is the taste
    /// call; quality tier still outranks all of it.
    static let shortlist = ["Serena", "Daniel", "Samantha", "Ava", "Moira",
                            "Karen", "Tessa", "Fiona", "Allison", "Susan"]

    static func rank(_ v: AVSpeechSynthesisVoice) -> (Int, Int) {
        let idx = shortlist.firstIndex(where: { v.name.contains($0) }) ?? shortlist.count
        return (-v.quality.rawValue, idx)      // lower sorts first
    }

    static func candidates() -> [AVSpeechSynthesisVoice] {
        AVSpeechSynthesisVoice.speechVoices()
            .filter { $0.language.hasPrefix("en") }
            .sorted { rank($0) < rank($1) }
    }

    static func bestVoice() -> AVSpeechSynthesisVoice? {
        if let saved = UserDefaults.standard.string(forKey: preferenceKey),
           let v = AVSpeechSynthesisVoice(identifier: saved) {
            return v
        }
        return candidates().first ?? AVSpeechSynthesisVoice(language: "en-US")
    }

    static func setVoice(_ nameOrId: String) -> AVSpeechSynthesisVoice? {
        let hit = candidates().first {
            $0.identifier == nameOrId
                || $0.name.lowercased() == nameOrId.lowercased()
                || $0.name.lowercased().contains(nameOrId.lowercased())
        }
        if let v = hit {
            UserDefaults.standard.set(v.identifier, forKey: preferenceKey)
        }
        return hit
    }

    /// True when this Mac has nothing better than compact voices installed —
    /// the single biggest thing standing between him and sounding human.
    static var stuckOnCompactVoices: Bool {
        !AVSpeechSynthesisVoice.speechVoices().contains { $0.quality.rawValue > 1 }
    }
}
