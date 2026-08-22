// main.swift — entry point.
//
// Swift requires top-level statements to live in a file called main.swift once
// a target has more than one source file. Everything else is types.

import Cocoa

// `casper --say "text"` speaks and prints the amplitude envelope it drove the
// mouth with. This is the proof that the animation follows the audio: if the
// envelope is flat or empty, the mouth is lying.
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
