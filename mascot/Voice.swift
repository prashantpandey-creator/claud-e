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
    private var taskStartedAt = Date()
    /// Age of the current recognition task — observable, because the failure
    /// this guards against is invisible: on-device tasks die quietly after
    /// about a minute of audio, and a dead task looks exactly like silence.
    var taskAge: TimeInterval { Date().timeIntervalSince(taskStartedAt) }
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
        taskStartedAt = Date()
        task = rec.recognitionTask(with: req) { [weak self] result, error in
            guard let self = self else { return }
            if let r = result {
                self.partial = r.bestTranscription.formattedString
                self.onPartial?(self.partial)
            }
            if let e = error as NSError?, e.code != 301 {   // 301 = we cancelled
                self.lastError = "\(e.localizedDescription)"
                // Words already recognised are not the task's to take with it:
                // if it dies mid-monologue, deliver what it heard instead of
                // discarding it with the corpse.
                let salvaged = self.partial
                    .trimmingCharacters(in: .whitespacesAndNewlines)
                self.partial = ""
                if salvaged.count >= 12, !self.muted {
                    DispatchQueue.main.async { self.onUtterance?(salvaged) }
                }
                // A dead task is not deafness-forever: give the recogniser a
                // beat and start a fresh one. Without this, any error left him
                // silently deaf until relaunch.
                DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) {
                    guard self.running, let r2 = self.recognizer else { return }
                    // ...unless someone (the 45s recycler, an end-of-turn)
                    // already started a fresh task — restarting again here
                    // would kill it and eat the first words spoken into it.
                    guard self.taskAge > 1.5 else { return }
                    self.beginTurn(r2)
                }
            }
        }
    }

    /// A turn ends when YOU go quiet, not when the recogniser decides. Waiting
    /// for isFinal makes him feel deaf for seconds after you stop.
    private func checkEndOfTurn() {
        guard running, !muted else { return }
        // Apple's on-device buffer recognition dies quietly at roughly one
        // minute per task. A task was only recycled after a completed
        // utterance — so a minute of you NOT talking killed the ear, and
        // everything after that looked like you were never heard. Recycle
        // any silent task well before the limit.
        if taskAge > 45, partial.isEmpty, let rec = recognizer {
            beginTurn(rec)
            return
        }
        let text = partial.trimmingCharacters(in: .whitespacesAndNewlines)
        guard text.count >= 2 else { return }
        guard quietFor > endOfTurnGap else { return }
        partial = ""
        if let rec = recognizer { beginTurn(rec) }      // fresh transcript next turn
        onUtterance?(text)
    }

    /// A fresh transcript, safe to call any time. Used after he finishes
    /// speaking: the old partial may hold fragments heard before the mute.
    func freshTurn() {
        guard running, let rec = recognizer else { return }
        beginTurn(rec)
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

/// Noises, not words.
///
/// "Wooo" read by a text-to-speech engine comes out as the WORD "woo", flat.
/// A real whoop is a pitch GLIDE, so these are generated: a sine that slides,
/// with a little vibrato and a soft envelope. For a ghost, a theremin is not
/// a compromise — it is the right instrument.
enum Whoop {
    case woo          // the classic: up, hang, down
    case boing        // a descending wobble, for a hop
    case hum          // two bored descending notes
    case yay          // a quick rising trill

    /// Rendered at the rate the caller is already wired for, so playing one
    /// never forces the player node to reconnect mid-conversation.
    func buffer(rate: Double = 24000) -> AVAudioPCMBuffer? {
        let dur: Double
        switch self {
        case .woo:   dur = 0.85
        case .boing: dur = 0.45
        case .hum:   dur = 0.90
        case .yay:   dur = 0.55
        }
        guard let fmt = AVAudioFormat(commonFormat: .pcmFormatFloat32,
                                      sampleRate: rate, channels: 1,
                                      interleaved: false),
              let buf = AVAudioPCMBuffer(pcmFormat: fmt,
                                         frameCapacity: AVAudioFrameCount(dur * rate)),
              let ch = buf.floatChannelData?[0]
        else { return nil }
        let n = Int(dur * rate)
        buf.frameLength = AVAudioFrameCount(n)

        var phase = 0.0
        for i in 0..<n {
            let t = Double(i) / rate
            let u = t / dur                     // 0..1 through the sound
            var f: Double
            var amp: Double
            switch self {
            case .woo:
                // up fast, hang, slide down — the shape of a shout
                f = u < 0.25 ? 280 + 520 * (u / 0.25)
                  : u < 0.55 ? 800
                  : 800 - 430 * ((u - 0.55) / 0.45)
                amp = sin(.pi * min(1, u * 1.25))          // soft in, soft out
            case .boing:
                f = 620 * pow(0.35, u) + 90 * sin(u * 38)  // descending wobble
                amp = pow(1 - u, 1.6)
            case .hum:
                f = u < 0.5 ? 320 : 250                    // mm-mm, resigned
                amp = (u < 0.5 ? sin(.pi * (u / 0.5))
                               : sin(.pi * ((u - 0.5) / 0.5))) * 0.8
            case .yay:
                f = 420 + 380 * u + 55 * sin(u * 46)       // rising trill
                amp = sin(.pi * u)
            }
            f *= 1 + 0.012 * sin(t * 33)                   // a little life
            phase += 2 * .pi * f / rate
            // a touch of second harmonic: a pure sine is a test tone
            let v = sin(phase) * 0.82 + sin(phase * 2) * 0.18
            ch[i] = Float(v * amp * 0.42)
        }
        return buf
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
    /// The format the player node is currently wired for.
    private var nodeFormat: AVAudioFormat?

    override init() {
        super.init()
        synth.delegate = self
    }

    /// Which lane produced the last utterance — "kokoro" or "apple".
    /// Observable so nobody has to guess whether the good voice is actually
    /// the one talking.
    private(set) var lane = "apple"

    /// Start the voice server if nobody has. Once per launch.
    ///
    /// Nothing else starts it. Casper only ASKED it to render, and when it was
    /// not running he fell silently back to Apple's voice — which measured
    /// 122 Hz against Kokoro's 205, i.e. the opposite of what was asked for.
    /// A fallback nobody is told about is a setting nobody chose.
    private static var triedToStart = false
    static func ensureVoiceServer() {
        guard !triedToStart else { return }
        triedToStart = true
        let skill = ("~/.claude/skills/meditate" as NSString).expandingTildeInPath
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        // python3.10 specifically: onnxruntime ships no wheel for 3.14, so the
        // interpreter this app's helpers use cannot load the model at all.
        p.arguments = ["python3.10", skill + "/tts.py", "--serve"]
        p.standardOutput = FileHandle.nullDevice
        p.standardError = FileHandle.nullDevice
        try? p.run()          // if python3.10 is absent, Apple's voice stands in
    }

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
    /// Nothing may start speaking while something else is. `speaking` was set
    /// but never CHECKED, and the two lanes are separate audio paths — the
    /// Kokoro buffer plays through our own engine, Apple's plays through
    /// AVSpeechSynthesizer. Two say() calls a second apart therefore came out
    /// as two voices talking over each other. This is set synchronously, on
    /// the caller's thread, because the render itself is async and takes about
    /// 0.8s: a flag set inside the async block leaves the same hole open.
    private var inFlight = false
    /// The newest thing he was asked to say while busy. Only one is kept —
    /// his lines are status, and stale status is worse than silence.
    private var queued: String?

    /// The queued line, already rendered, waiting for the current one to end.
    ///
    /// Without this a queued sentence was not sent to Kokoro until the first
    /// had finished PLAYING, so every two-part answer carried a render's worth
    /// of silence in its middle: measured 0.61s by `casper --saytwice`. The
    /// render can perfectly well happen while the first line is still in the
    /// air — the voice server is free the moment it hands back a buffer.
    private var queuedBuf: AVAudioPCMBuffer?
    /// Which text queuedBuf belongs to, so a newer queued line never plays a
    /// stale buffer. `queued` is newest-wins and can change mid-render.
    private var queuedFor: String?
    /// Bumped on every say() and every shutUp(). A render whose generation is
    /// stale must never reach the speakers.
    private var generation: UInt64 = 0

    func say(_ text: String) {
        let clean = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !clean.isEmpty else { return }
        if inFlight {
            queued = clean
            queuedBuf = nil
            queuedFor = nil
            let mine = generation
            DispatchQueue.global().async { [weak self] in
                guard let self = self else { return }
                guard let buf = self.kokoroRender(clean) else { return }
                DispatchQueue.main.async {
                    // Still the same conversation, and still the same queued
                    // line? A newer one may have replaced it while this
                    // rendered, and shutUp() may have ended the whole thing.
                    guard mine == self.generation, self.queued == clean else { return }
                    self.queuedBuf = buf
                    self.queuedFor = clean
                }
            }
            return
        }
        inFlight = true
        generation &+= 1
        let mine = generation
        // Kokoro first: rendered off the main thread, whole utterance in one
        // call (it does its own prosody and pauses — our splicing is for the
        // Apple lane). Falls back to Apple in one timeout when the server is
        // down, so he is never mute, just less human for a while.
        DispatchQueue.global().async { [weak self] in
            guard let self = self else { return }
            Mouth.ensureVoiceServer()
            // One retry. The server is single-threaded, so a second render
            // arriving mid-first gets refused — and a refusal used to mean a
            // whole sentence in the wrong voice.
            var rendered = self.kokoroRender(clean)
            if rendered == nil {
                Thread.sleep(forTimeInterval: 0.35)
                rendered = self.kokoroRender(clean)
            }
            if let buf = rendered {
                DispatchQueue.main.async {
                    // The render takes ~0.9s and CANNOT be interrupted. If you
                    // pressed Mute, Quiet or the X while it ran, shutUp() froze
                    // the player but this block still landed and spoke — audio
                    // out of a Casper whose bubble read "Muted."
                    guard mine == self.generation else { return }
                    self.lane = "kokoro"
                    self.play([buf])
                }
            } else {
                DispatchQueue.main.async {
                    guard mine == self.generation else { return }
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
        // Younger, not squeaky. macOS ships no child voice, so the young
        // register comes from lifting the pitch of a male voice — past about
        // 1.35 it stops sounding like a boy and starts sounding like a
        // chipmunk. MEDITATE_VOICE_PITCH overrides for taste.
        let envPitch = ProcessInfo.processInfo.environment["MEDITATE_VOICE_PITCH"]
        u.pitchMultiplier = Float(envPitch ?? "") ?? 1.28
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
            // An empty render is still the end of the turn. Returning without
            // releasing left inFlight true and he never spoke again.
            if acc.isEmpty { finished() } else { play(acc) }
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
        guard let first = chunks.first else { finished(); return }
        guard let joined = Mouth.concat(chunks, format: first.format) else {
            finished(); return
        }

        // Reconnect when the sample rate changes. The node was attached once,
        // with the format of whichever lane happened to speak FIRST, and never
        // reconnected — Kokoro is 24000 Hz and Apple is 22050 Hz, so after any
        // fallback af_heart played through a 22050-pinned node: 9% slow and
        // about 1.5 semitones flat. That is why the voice that "was good"
        // stopped sounding like itself.
        if attached, nodeFormat?.sampleRate != joined.format.sampleRate {
            player.stop()
            engine.disconnectNodeOutput(player)
            engine.connect(player, to: engine.mainMixerNode, format: joined.format)
            nodeFormat = joined.format
        }
        if !attached {
            engine.attach(player)
            engine.connect(player, to: engine.mainMixerNode, format: joined.format)
            nodeFormat = joined.format
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
        } catch { finished(); return }

        speaking = true
        onStart?()
        player.scheduleBuffer(joined, at: nil, options: []) { [weak self] in
            DispatchQueue.main.async {
                guard let self = self else { return }
                self.speaking = false
                self.drive = 0
                self.finished()
                self.onFinish?()
            }
        }
        player.play()
    }

    /// One utterance is over. Free the lane, then say the newest thing that
    /// arrived while it was busy.
    private func finished() {
        inFlight = false
        guard let next = queued else { return }
        queued = nil
        // Pre-rendered while the last line was playing: speak it now, with no
        // render sitting between the two halves of one answer.
        if let buf = queuedBuf, queuedFor == next {
            queuedBuf = nil
            queuedFor = nil
            inFlight = true
            generation &+= 1
            lane = "kokoro"
            play([buf])
            return
        }
        queuedBuf = nil
        queuedFor = nil
        say(next)               // render was not ready — the old path, intact
    }

    /// Make a noise. Goes through the same lane as speech, so it queues
    /// behind a sentence rather than talking over it.
    func makeNoise(_ w: Whoop) {
        if inFlight { return }              // never over a real sentence
        inFlight = true
        generation &+= 1
        let rate = nodeFormat?.sampleRate ?? 24000
        guard let buf = w.buffer(rate: rate) else { inFlight = false; return }
        lane = "whoop"
        play([buf])
    }

    func shutUp() {
        queued = nil
        queuedBuf = nil
        queuedFor = nil
        inFlight = false
        generation &+= 1          // anything still rendering is now stale
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
    // The two lanes MUST agree about who he is.
    //
    // Kokoro speaks as af_heart, female. This shortlist put Daniel — en-GB,
    // male — first, so every time the Kokoro server was busy or cold the
    // fallback answered in a different person's voice. That is the "one woman,
    // and when I talk, a man": not two voices at once, two voices taking
    // turns, and nothing anywhere said the speaker had changed.
    //
    // If the Kokoro voice is ever changed to a male one, change this too.
    static let shortlist = ["Samantha", "Serena", "Ava", "Zoe", "Karen",
                            "Moira", "Fiona", "Allison", "Susan", "Victoria"]

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
