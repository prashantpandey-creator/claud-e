// main.swift — entry point.
//
// Swift requires top-level statements to live in a file called main.swift once
// a target has more than one source file. Everything else is types.

import Cocoa
import Speech

// `casper --say "text"` speaks and prints the amplitude envelope it drove the
// mouth with. This is the proof that the animation follows the audio: if the
// envelope is flat or empty, the mouth is lying.
// `casper --hear "casper what should I work on"` runs a spoken utterance
// through the REAL chain — addressing rule, advisor, mouth — without needing
// a microphone. It is how the companion's wiring gets checked end to end.
// `casper --transcribe <audiofile>` proves on-device recognition works on THIS
// machine, without needing anyone to talk. It runs inside the bundle because a
// bare binary cannot request speech authorisation at all — it aborts.
// `casper --voices`            list what this Mac can speak with, best first
// `casper --audition`          say the same line in the top candidates
// `casper --voice Serena`      remember one
//
// "Soothing" is a taste call, not a measurement, so the only honest way to
// choose is to hear them side by side.
if CommandLine.arguments.count > 1,
   ["--voices", "--audition", "--voice"].contains(CommandLine.arguments[1]) {
    _ = NSApplication.shared
    let mode = CommandLine.arguments[1]

    if Mouth.stuckOnCompactVoices {
        print("NOTE: every voice installed here is the compact tier. That is the")
        print("      ceiling on how human he can sound — no rate or pitch setting")
        print("      fixes it. System Settings > Accessibility > Spoken Content >")
        print("      System Voice > Manage Voices, then pick any (Enhanced) or")
        print("      (Premium) English voice. Roughly 100-500 MB each, one time.")
        print("")
    }

    if mode == "--voice", CommandLine.arguments.count > 2 {
        if let v = Mouth.setVoice(CommandLine.arguments[2]) {
            print("voice set: \(v.name) (\(v.language))")
        } else {
            print("no voice matching \(CommandLine.arguments[2])")
            exit(1)
        }
        exit(0)
    }

    let top = Array(Mouth.candidates().prefix(mode == "--voices" ? 40 : 6))
    if mode == "--voices" {
        let tier = ["", "compact", "enhanced", "premium"]
        for v in top {
            print(String(format: "  %-10s %-6s %@", (v.name as NSString).utf8String!,
                         (v.language as NSString).utf8String!,
                         tier[min(3, v.quality.rawValue)]))
        }
        exit(0)
    }

    // audition: one line, each voice, in order of preference
    let line = "Payments are the one thing actually bleeding. "
             + "Want me to go find where that key dropped?"
    var i = 0
    let m = Mouth()
    // The audition must not CHANGE anything: it sets each candidate as the
    // live voice to render the sample, so the prior preference has to be put
    // back afterwards. Without this, whoever ran the audition last had pinned
    // its final voice — compact Rishi — as the permanent preference.
    let prior = UserDefaults.standard.string(forKey: Mouth.preferenceKey)
    func restore() {
        if let p = prior {
            UserDefaults.standard.set(p, forKey: Mouth.preferenceKey)
        } else {
            UserDefaults.standard.removeObject(forKey: Mouth.preferenceKey)
        }
    }
    func next() {
        guard i < top.count else { restore(); exit(0) }
        let v = top[i]; i += 1
        print("  \(i). \(v.name) (\(v.language)) — \(v.identifier)")
        UserDefaults.standard.set(v.identifier, forKey: Mouth.preferenceKey)
        m.say(line)
        var spoke = false
        let t0 = Date()
        Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { tm in
            if m.speaking { spoke = true }
            if (spoke && !m.speaking) || Date().timeIntervalSince(t0) > 20 {
                tm.invalidate()
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { next() }
            }
        }
    }
    print("auditioning \(top.count) voices — say `casper --voice <name>` to keep one\n")
    next()
    RunLoop.main.run()
}

// `casper --notify` posts one real completion notice through the same path a
// finished agent uses, so the CLICK can be checked by a person — the one part
// of this that no script can verify.
if CommandLine.arguments.count > 1, CommandLine.arguments[1] == "--notify" {
    let app = NSApplication.shared
    app.setActivationPolicy(.accessory)
    Notifier.shared.start()
    DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) {
        Notifier.shared.post(title: "Mila live is done",
                             body: "Click to open the dashboard.")
    }
    DispatchQueue.main.asyncAfter(deadline: .now() + 25) { exit(0) }
    app.run()
}

if CommandLine.arguments.count > 2, CommandLine.arguments[1] == "--transcribe" {
    _ = NSApplication.shared
    guard let rec = SFSpeechRecognizer(locale: Locale(identifier: "en-US")) else {
        print("no recogniser"); exit(1)
    }
    print("available=\(rec.isAvailable) onDevice=\(rec.supportsOnDeviceRecognition)")
    let req = SFSpeechURLRecognitionRequest(
        url: URL(fileURLWithPath: CommandLine.arguments[2]))
    req.requiresOnDeviceRecognition = true
    req.contextualStrings = Ear.vocabulary
    print("vocabulary=\(Ear.vocabulary.count) terms")
    rec.recognitionTask(with: req) { result, error in
        if let e = error { print("ERROR: \(e.localizedDescription)"); exit(1) }
        if let r = result, r.isFinal {
            print("transcript: \(r.bestTranscription.formattedString)")
            exit(0)
        }
    }
    DispatchQueue.main.asyncAfter(deadline: .now() + 40) {
        print("TIMED OUT — recogniser never returned"); exit(1)
    }
    RunLoop.main.run()
}

// What a bare answer means while an offer is on screen, and what he remembers
// about being told no. Headless, because the alternative is proving it by
// talking at a live window, which is the same as not proving it.
//
//   casper --answer "not now"        -> no
//   casper --decline "meditate go"   -> records one refusal
//   casper --declined "meditate go"  -> suppressed n=1 hushed=no
if CommandLine.arguments.count > 2, CommandLine.arguments[1] == "--answer" {
    let a = answerToPendingOffer(CommandLine.arguments[2])
    print(a == nil ? "-" : (a! ? "yes" : "no"))
    exit(0)
}
if CommandLine.arguments.count > 2, CommandLine.arguments[1] == "--decline" {
    Declines.record(CommandLine.arguments[2])
    print(Declines.describe(CommandLine.arguments[2]))
    exit(0)
}
if CommandLine.arguments.count > 2, CommandLine.arguments[1] == "--declined" {
    print(Declines.describe(CommandLine.arguments[2]))
    exit(0)
}

if CommandLine.arguments.count > 2, CommandLine.arguments[1] == "--hear" {
    _ = NSApplication.shared
    let said = CommandLine.arguments[2]
    guard let q = addressedQuestion(said, armed: false) else {
        print("NOT ADDRESSED -> stays quiet (correct for overheard talk)")
        exit(0)
    }
    print("heard:     \(said)")
    print("question:  \(q)")
    // the SAME decision the live mascot makes — proving the guarantees here
    // proves them there
    switch routeDecision(q) {
    case .refuse(let line):
        print("route:     REFUSED (code, not prompt)")
        print("answer:    \(line)")
        exit(0)
    case .offer(let verb, let line):
        print("route:     OFFER meditate \(verb) — nothing runs without Yes")
        print("answer:    \(line)")
        exit(0)
    case .advise:
        print("route:     advisor")
    }
    let answer = Meditate.advise(q)
    print("answer:    \(answer.isEmpty ? "(empty)" : answer)")
    guard !answer.isEmpty else { print("CHAIN BROKE: advisor returned nothing"); exit(1) }
    let m = Mouth()
    var spoke = false
    m.onStart = { spoke = true }
    m.say(answer)
    let t0 = Date()
    Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { tm in
        if (spoke && !m.speaking) || Date().timeIntervalSince(t0) > 25 {
            tm.invalidate()
            print("spoke:     \(spoke ? "yes" : "NO — mouth never started")")
            exit(spoke ? 0 : 1)
        }
    }
    RunLoop.main.run()
}

// `casper --saytwice` fires two utterances a beat apart and counts how many
// actually START. Two was the bug the owner heard: the lanes are separate
// audio paths, so two concurrent says came out as two voices at once.
// `casper --saycancel` starts an utterance and stops it MID-RENDER. Nothing
// may reach the speakers: pressing Mute while a render is running used to
// leave the render to land and speak anyway.
// `casper --bored` shows what he says as boredom climbs, and how long he
// waits between grumbles. Verifying this by sitting still for twenty minutes
// is not verification, it is waiting.
// `casper --whoop` plays each noise and MEASURES its pitch sweep, because a
// whoop that does not glide is just a beep.
if CommandLine.arguments.count > 1, CommandLine.arguments[1] == "--whoop" {
    _ = NSApplication.shared
    let play = CommandLine.arguments.contains("--play")
    let mouth = Mouth()
    for (name, w) in [("woo", Whoop.woo), ("boing", .boing),
                      ("hum", .hum), ("yay", .yay)] {
        guard let b = w.buffer(), let ch = b.floatChannelData?[0] else { continue }
        let n = Int(b.frameLength)
        // zero crossings per window -> rough pitch, start vs middle vs end
        func pitch(_ from: Int, _ to: Int) -> Double {
            var crossings = 0
            for i in (from + 1)..<to where (ch[i - 1] < 0) != (ch[i] < 0) { crossings += 1 }
            return Double(crossings) / 2 / (Double(to - from) / b.format.sampleRate)
        }
        let a = pitch(0, n / 4), m = pitch(n * 2 / 5, n * 3 / 5), z = pitch(n * 3 / 4, n - 1)
        var peak: Float = 0
        for i in 0..<n { peak = max(peak, abs(ch[i])) }
        // %s takes a C string; handing it a Swift String is undefined and
        // segfaults. Numbers through String(format:), words plain.
        let nums = String(format: "%.2fs  pitch %4.0f -> %4.0f -> %4.0f Hz  peak %.2f",
                          Double(n) / b.format.sampleRate, a, m, z, peak)
        let verdict = (abs(a - z) > 60 || abs(a - m) > 60) ? "GLIDES" : "flat — a beep"
        print("  " + name.padding(toLength: 6, withPad: " ", startingAt: 0)
              + " " + nums + "  " + verdict)
        if play {
            mouth.makeNoise(w)
            // let it finish before the next one, so they do not stack
            RunLoop.main.run(until: Date().addingTimeInterval(1.4))
        }
    }
    exit(0)
}

if CommandLine.arguments.count > 1, CommandLine.arguments[1] == "--bored" {
    _ = NSApplication.shared
    let d = App()
    d.shotMode = true
    d.applicationDidFinishLaunching(Notification(name: Notification.Name("bored")))
    d.canInterruptNow = true
    d.lastInteractionAt = Date().addingTimeInterval(-600)   // ten minutes ignored
    var spoken: [String] = []
    d.onSpeakForTest = { spoken.append($0) }
    for _ in 0..<6 {
        d.nextGrumbleAt = Date().addingTimeInterval(-1)     // due now
        d.grumble()
    }
    print("he grumbles, in order:")
    for (i, l) in spoken.enumerated() { print("  \(i + 1). \(l)") }
    print("\nand then goes quiet for \(Int(d.nextGrumbleAt.timeIntervalSinceNow / 60))m")
    // the gates
    d.noticedYou()
    d.nextGrumbleAt = Date().addingTimeInterval(-1)
    d.canInterruptNow = false
    let before = spoken.count
    d.grumble()
    print("while you are typing:      \(spoken.count == before ? "silent (correct)" : "SPOKE — wrong")")
    d.canInterruptNow = true
    d.voiceOff = true
    d.lastInteractionAt = Date().addingTimeInterval(-600)
    d.nextGrumbleAt = Date().addingTimeInterval(-1)
    d.grumble()
    print("while muted:               \(spoken.count == before ? "silent (correct)" : "SPOKE — wrong")")
    d.voiceOff = false
    exit(0)
}

if CommandLine.arguments.count > 1, CommandLine.arguments[1] == "--saycancel" {
    _ = NSApplication.shared
    let m = Mouth()
    var starts = 0
    m.onStart = { starts += 1 }
    m.say("This line was cancelled and must never be heard.")
    // the render takes ~0.9s; stop well inside it
    DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) { m.shutUp() }
    DispatchQueue.main.asyncAfter(deadline: .now() + 6) {
        print("starts after cancel = \(starts)")
        print(starts == 0 ? "CANCELLED — the stale render never played"
                          : "LEAKED — it spoke after being stopped")
        exit(starts == 0 ? 0 : 1)
    }
    RunLoop.main.run()
}

if CommandLine.arguments.count > 1, CommandLine.arguments[1] == "--saytwice" {
    _ = NSApplication.shared
    let m = Mouth()
    var starts = 0
    var finishes = 0
    m.onStart = { starts += 1 }
    m.onFinish = { finishes += 1 }
    m.say("First line, the one you should hear.")
    DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
        m.say("Second line, which must wait its turn.")
    }
    let t0 = Date()
    Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { tm in
        // both should eventually play, but never at the same moment
        if finishes >= 2 || Date().timeIntervalSince(t0) > 30 {
            tm.invalidate()
            print("starts=\(starts) finishes=\(finishes)")
            print(starts == finishes
                  ? "SERIALISED — never two at once"
                  : "OVERLAP — \(starts - finishes) started before the last finished")
            exit(0)
        }
        if starts - finishes > 1 {
            tm.invalidate()
            print("OVERLAP: \(starts) started, only \(finishes) finished")
            exit(1)
        }
    }
    RunLoop.main.run()
}

if CommandLine.arguments.count > 2, CommandLine.arguments[1] == "--say" {
    _ = NSApplication.shared
    let m = Mouth()
    var samples: [Double] = []
    var started = false
    m.onStart = { started = true }
    m.say(CommandLine.arguments[2])
    let t0 = Date()
    Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { tm in
        if m.speaking { samples.append(Double(m.drive)) }
        let done = (started && !m.speaking && samples.count > 4)
        if done || Date().timeIntervalSince(t0) > 30 {
            tm.invalidate()
            // longest run of near-silence: the proof that the pauses between
            // sentences actually reached the audio, rather than just the code
            var run = 0, longest = 0
            for s in samples {
                if s < 0.03 { run += 1; longest = max(longest, run) } else { run = 0 }
            }
            print(String(format: "longest quiet run: %.2fs  %@",
                         Double(longest) * 0.05,
                         longest >= 3 ? "(sentence pauses present)" : "(NO pauses)"))
            let nz = samples.filter { $0 > 0.02 }
            let mx = samples.max() ?? 0
            let mean = samples.isEmpty ? 0 : samples.reduce(0,+) / Double(samples.count)
            print("lane=" + m.lane)
            print(String(format: "started=%@ samples=%d nonzero=%d peak=%.3f mean=%.3f",
                         started ? "yes" : "no", samples.count, nz.count, mx, mean))
            // a real envelope moves; a fake one is constant
            let spread = mx - (samples.min() ?? 0)
            print(String(format: "spread=%.3f  %@", spread,
                         spread > 0.05 ? "ENVELOPE MOVES (audio-driven)"
                                       : "FLAT — not audio-driven"))
            exit(0)
        }
    }
    RunLoop.main.run()
}

// `casper --shot <out.png> [bubble text]` — a screenshot of the whole widget.
// (Header reconstructed: this block arrived uncommitted from a concurrent
// session and an over-wide edit of mine ate its first two lines.)
if CommandLine.arguments.count > 2, CommandLine.arguments[1] == "--shot" {
    let app = NSApplication.shared
    let d = App()
    d.shotMode = true                    // no mic, no timers doing work
    app.delegate = d
    d.applicationDidFinishLaunching(Notification(name: Notification.Name("shot")))
    if CommandLine.arguments.count > 3 {
        d.setBubble(CommandLine.arguments[3])
    }
    if CommandLine.arguments.contains("--offer") { d.showOffer(true) }
    // real rows, so the review frame shows the real thing
    d.layoutFleet(Meditate.fleet().map {
        FleetView.Row(goal: d.pretty($0.goal), ticked: $0.ticked,
                      mins: $0.mins, window: $0.window, alive: $0.alive)
    })
    for _ in 0..<120 { d.ghost.tick(dt: 1.0 / 60) }
    guard let root = d.window.contentView,
          let rep = root.bitmapImageRepForCachingDisplay(in: root.bounds) else {
        print("no view"); exit(1)
    }
    rep.size = root.bounds.size
    root.cacheDisplay(in: root.bounds, to: rep)
    if let png = rep.representation(using: .png, properties: [:]) {
        try? png.write(to: URL(fileURLWithPath: CommandLine.arguments[2]))
        print("wrote \(CommandLine.arguments[2])")
    }
    exit(0)
}

if CommandLine.arguments.count > 2, CommandLine.arguments[1] == "--frames" {
    _ = NSApplication.shared
    let secs = Double(CommandLine.arguments[2]) ?? 5
    let n = Int(secs * 60)
    let v = GhostView(frame: NSRect(x: 0, y: 0, width: 200, height: 200))
    v.mood = .idle
    for _ in 0..<n { v.tick(dt: 1.0 / 60) }
    let v2 = GhostView(frame: NSRect(x: 0, y: 0, width: 200, height: 200))
    v2.mood = .speaking
    v2.mouthDrive = 0.5
    for _ in 0..<n { v2.tick(dt: 1.0 / 60) }
    print(String(format: "idle:     %d ticks -> %d repaints (%.0f%%)",
                 v.framesSeen, v.framesDrawn,
                 100.0 * Double(v.framesDrawn) / Double(max(1, v.framesSeen))))
    print(String(format: "speaking: %d ticks -> %d repaints (%.0f%%)",
                 v2.framesSeen, v2.framesDrawn,
                 100.0 * Double(v2.framesDrawn) / Double(max(1, v2.framesSeen))))
    exit(0)
}

if CommandLine.arguments.count > 2, CommandLine.arguments[1] == "--render" {
    _ = NSApplication.shared                     // AppKit needs to exist to draw
    renderFrames(to: CommandLine.arguments[2])
    exit(0)
}

let app = NSApplication.shared
let delegate = App()
app.delegate = delegate
app.run()
