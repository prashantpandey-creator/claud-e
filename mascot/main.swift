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

if CommandLine.arguments.count > 2, CommandLine.arguments[1] == "--hear" {
    _ = NSApplication.shared
    let said = CommandLine.arguments[2]
    guard let q = addressedQuestion(said, armed: false) else {
        print("NOT ADDRESSED -> stays quiet (correct for overheard talk)")
        exit(0)
    }
    print("heard:     \(said)")
    print("question:  \(q)")
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
        if done || Date().timeIntervalSince(t0) > 12 {
            tm.invalidate()
            let nz = samples.filter { $0 > 0.02 }
            let mx = samples.max() ?? 0
            let mean = samples.isEmpty ? 0 : samples.reduce(0,+) / Double(samples.count)
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

if CommandLine.arguments.count > 2, CommandLine.arguments[1] == "--render" {
    _ = NSApplication.shared                     // AppKit needs to exist to draw
    renderFrames(to: CommandLine.arguments[2])
    exit(0)
}

let app = NSApplication.shared
let delegate = App()
app.delegate = delegate
app.run()
