// Casper — the desktop companion. A real floating ghost, not a browser tab.
//
// A borderless, transparent, always-on-top window that drifts at the edge of
// your screen. It brightens when it has something to tell you, speaks aloud,
// and asks before it does anything: "I found this. Want me to fix it?" —
// Yes runs the same command the CLI runs; No leaves it alone.
//
// It never decides for you. Every action is an offer with a visible answer.
//
// build: swiftc -O -o casper Casper.swift -framework Cocoa -framework AVFoundation

import Cocoa
import AVFoundation

// MARK: - talking to meditate (the same commands the terminal runs)

struct Brief {
    var headline: String
    var action: String      // "meditate fix" / "meditate go" / ""
    var kind: String        // repair | goals | task | still | clear
    var canInterrupt: Bool
    var facts: Int
    var verified: Int
}

final class Meditate {
    static let skillDir = ("~/.claude/skills/meditate" as NSString).expandingTildeInPath

    /// Run a meditate module and return stdout. Never throws into the UI.
    static func run(_ args: [String], timeout: TimeInterval = 60) -> String {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        p.arguments = ["python3"] + args
        p.currentDirectoryURL = URL(fileURLWithPath: skillDir)
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = Pipe()
        do { try p.run() } catch { return "" }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        return String(data: data, encoding: .utf8) ?? ""
    }

    /// Ask the server first — that is the thing the owner watches. Falling
    /// back to a subprocess keeps him alive when the server is down, but then
    /// Pulse cannot see what he is looking at, so the server comes first.
    static func briefFromServer() -> Brief? {
        guard let url = URL(string: "http://127.0.0.1:7711/api/state") else { return nil }
        var req = URLRequest(url: url)
        req.setValue("1", forHTTPHeaderField: "X-Meditate")
        req.timeoutInterval = 6
        var out: Brief?
        let sem = DispatchSemaphore(value: 0)
        URLSession.shared.dataTask(with: req) { data, _, _ in
            defer { sem.signal() }
            guard let d = data,
                  let j = try? JSONSerialization.jsonObject(with: d) as? [String: Any],
                  let b = j["briefing"] as? [String: Any],
                  let t = j["timing"] as? [String: Any] else { return }
            out = Brief(headline: b["headline"] as? String ?? "",
                        action: b["action"] as? String ?? "",
                        kind: b["kind"] as? String ?? "clear",
                        canInterrupt: t["interrupt_ok"] as? Bool ?? false,
                        facts: 0, verified: 0)
        }.resume()
        _ = sem.wait(timeout: .now() + 8)
        return out
    }

    static func brief() -> Brief? {
        if let viaServer = briefFromServer() { return viaServer }
        let out = run([skillDir + "/voice.py", "--json"])
        guard let d = out.data(using: .utf8),
              let j = try? JSONSerialization.jsonObject(with: d) as? [String: Any],
              let data = j["data"] as? [String: Any],
              let b = data["briefing"] as? [String: Any],
              let t = data["timing"] as? [String: Any] else { return nil }
        return Brief(headline: b["headline"] as? String ?? "",
                     action: b["action"] as? String ?? "",
                     kind: b["kind"] as? String ?? "clear",
                     canInterrupt: t["interrupt_ok"] as? Bool ?? false,
                     facts: 0, verified: 0)
    }

    /// Ask Casper's reasoning brain a real question (headless claude).
    static func advise(_ question: String) -> String {
        let out = run([skillDir + "/advisor.py", question], timeout: 90)
        return out.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// Run an offered action THROUGH THE SERVER, so every action Casper takes
    /// is visible in Pulse's activity trail — same endpoint the dashboard
    /// buttons use, one record of everything that happened. Falls back to
    /// running it directly if the server isn't up, and says so.
    static func perform(_ action: String) -> String {
        let verb = action.contains("fix") ? "fix"
                 : action.contains("grade") ? "grade" : "go"
        if let viaServer = postAct(verb) { return viaServer }
        let direct: String
        switch verb {
        case "fix":   direct = run([skillDir + "/go.py", "--repair-only"])
        case "grade": direct = run([skillDir + "/nidra_bridge.py", "--sleep"])
        default:      direct = run([skillDir + "/go.py"])
        }
        return direct.isEmpty ? "Nothing to run." : direct
    }

    /// POST to the local Pulse server. nil when it isn't running.
    static func postAct(_ verb: String) -> String? {
        guard let url = URL(string: "http://127.0.0.1:7711/api/act") else { return nil }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.timeoutInterval = 30
        req.setValue("1", forHTTPHeaderField: "X-Meditate")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(
            withJSONObject: ["action": verb, "arg": ""])
        var result: String? = nil
        let sem = DispatchSemaphore(value: 0)
        URLSession.shared.dataTask(with: req) { data, _, _ in
            defer { sem.signal() }
            guard let d = data,
                  let j = try? JSONSerialization.jsonObject(with: d) as? [String: Any]
            else { return }
            result = (j["output"] as? String) ?? "Done."
        }.resume()
        _ = sem.wait(timeout: .now() + 32)
        return result
    }
}

// MARK: - the ghost
//
// Everything that moves lives in tick(); draw() only draws. That split is the
// whole difference between a mascot that looks alive and one that looks
// broken — the previous mouth picked a new random height every frame, which
// reads as flicker, not speech.

final class GhostView: NSView {
    enum Mood { case idle, listening, thinking, speaking, alert }
    var mood: Mood = .idle
    var glow: CGFloat = 0.25          // 0.25 calm .. 1.0 has something to say

    // smoothed animation state — every one of these eases, none of them jump
    private var t: CGFloat = 0
    private var blink: CGFloat = 1            // 1 open .. 0 shut
    private var blinkAt: CGFloat = 2.5
    private var mouth: CGFloat = 0            // 0 closed .. 1 wide
    private var mouthTarget: CGFloat = 0
    private var gaze = CGPoint(x: 0, y: 0)
    private var gazeTo = CGPoint(x: 0, y: 0)
    private var gazeAt: CGFloat = 1.5
    private var lean: CGFloat = 0
    private var ring: CGFloat = 0

    override var isFlipped: Bool { true }

    /// Force the next frame to be mid-blink. Only render mode uses this —
    /// blinks are on a random schedule, so a review frame would never catch one.
    func forceBlink() { blinkAt = t - 0.001; blink = 0.18 }

    /// One frame of life. Called at 60Hz.
    func tick(dt: CGFloat) {
        t += dt

        // blink: shut fast, open slower, then wait a random beat
        if t > blinkAt {
            blink -= dt * 14
            if blink <= 0 { blink = 0; blinkAt = t + CGFloat.random(in: 2.2...5.5) }
        } else if blink < 1 {
            blink = min(1, blink + dt * 9)
        }

        // gaze: small saccades, eased. Thinking looks up and away.
        if mood == .thinking {
            gazeTo = CGPoint(x: -0.5, y: -0.6)
        } else if t > gazeAt {
            gazeTo = CGPoint(x: .random(in: -0.45...0.45), y: .random(in: -0.3...0.25))
            gazeAt = t + CGFloat.random(in: 1.4...3.6)
        }
        gaze.x += (gazeTo.x - gaze.x) * min(1, dt * 6)
        gaze.y += (gazeTo.y - gaze.y) * min(1, dt * 6)

        // mouth: a smoothed syllable oscillator, not per-frame noise
        if mood == .speaking {
            let syl = (sin(t * 11.5) * 0.5 + 0.5) * (sin(t * 4.3) * 0.35 + 0.65)
            mouthTarget = 0.25 + 0.75 * syl
        } else if mood == .listening {
            mouthTarget = 0.10
        } else {
            mouthTarget = 0
        }
        mouth += (mouthTarget - mouth) * min(1, dt * 16)

        // lean in when listening; pulse ring while he waits on you
        let leanTo: CGFloat = (mood == .listening) ? 1 : 0
        lean += (leanTo - lean) * min(1, dt * 5)
        ring = (mood == .listening || mood == .alert) ? (0.62 + 0.38 * sin(t * 2.4)) : 0

        needsDisplay = true
    }

    // palette: warm off-white body so he reads on any wallpaper, amber accent
    private let bodyTop = NSColor(srgbRed: 1.00, green: 0.99, blue: 0.97, alpha: 1)
    private let bodyBot = NSColor(srgbRed: 0.90, green: 0.87, blue: 0.83, alpha: 1)
    private let ink     = NSColor(srgbRed: 0.16, green: 0.16, blue: 0.20, alpha: 1)
    private let amber   = NSColor(srgbRed: 0.91, green: 0.71, blue: 0.25, alpha: 1)

    override func draw(_ dirty: NSRect) {
        guard let ctx = NSGraphicsContext.current?.cgContext else { return }
        let w = bounds.width, h = bounds.height
        let cx = w / 2

        // breathing: squash and stretch that preserves volume, so he never
        // looks like he is simply scaling up and down
        let breath = sin(t * 1.9) * 0.022
        let bob = sin(t * 1.9) * 3.5 - lean * 4
        var bw = w * 0.66 * (1 - breath)
        var bh = h * 0.70 * (1 + breath)
        bw *= 1 + glow * 0.02; bh *= 1 + glow * 0.02
        let ty = h * 0.10 + bob
        let left = cx - bw / 2

        // ---- ground shadow: what makes him sit in the world, not float on it
        ctx.saveGState()
        let sh = NSBezierPath(ovalIn: NSRect(x: cx - bw * 0.27,
                                             y: ty + bh + 3 - bob * 0.35,
                                             width: bw * 0.54, height: bh * 0.052))
        NSColor(white: 0, alpha: 0.10).setFill()
        ctx.setShadow(offset: .zero, blur: 7, color: NSColor(white: 0, alpha: 0.14).cgColor)
        sh.fill()
        ctx.restoreGState()

        // ---- attention ring: he is waiting on you
        if ring > 0.01 {
            for k in 0..<3 {
                let phase = ring - CGFloat(k) * 0.28
                guard phase > 0 else { continue }
                let spread = bw * (0.54 + CGFloat(k) * 0.11)
                let a = amber.withAlphaComponent(0.75 * phase * (1 - CGFloat(k) * 0.28))
                a.setStroke()
                for side: CGFloat in [-1, 1] {
                    let arc = NSBezierPath()
                    arc.appendArc(withCenter: NSPoint(x: cx + side * spread,
                                                      y: ty + bh * 0.42),
                                  radius: bw * (0.10 + CGFloat(k) * 0.045),
                                  startAngle: side > 0 ? -52 : 128,
                                  endAngle: side > 0 ? 52 : 232)
                    arc.lineWidth = 2.4
                    arc.lineCapStyle = .round
                    arc.stroke()
                }
            }
        }

        // ---- body
        let body = blob(cx: cx, left: left, top: ty, w: bw, h: bh)
        ctx.saveGState()
        ctx.setShadow(offset: CGSize(width: 0, height: 2), blur: 14,
                      color: NSColor(white: 0, alpha: 0.28).cgColor)
        NSGradient(colors: [bodyTop, bodyBot])?.draw(in: body, angle: -90)
        ctx.restoreGState()

        // warm rim where the glow lives — brightens when he has something
        body.lineWidth = 1.2
        amber.withAlphaComponent(0.12 + 0.45 * glow).setStroke()
        body.stroke()

        // glossy highlight, top-left
        ctx.saveGState()
        body.addClip()
        let glossRect = NSRect(x: left + bw * 0.04, y: ty - bh * 0.02,
                               width: bw * 0.62, height: bh * 0.44)
        NSGradient(starting: NSColor(white: 1, alpha: 0.62),
                   ending: NSColor(white: 1, alpha: 0.0))?
            .draw(in: NSBezierPath(ovalIn: glossRect),
                  relativeCenterPosition: NSPoint(x: -0.15, y: -0.25))
        ctx.restoreGState()

        // ---- face
        let eyeY = ty + bh * 0.40
        let eyeDX = bw * 0.21
        let eyeW = bw * 0.115, eyeH = eyeW * 1.45 * max(0.08, blink)
        for sx in [-eyeDX, eyeDX] {
            let ex = cx + sx + gaze.x * bw * 0.055
            let ey = eyeY + gaze.y * bh * 0.042
            let e = NSBezierPath(roundedRect:
                NSRect(x: ex - eyeW / 2, y: ey - eyeH / 2, width: eyeW, height: eyeH),
                xRadius: eyeW / 2, yRadius: min(eyeW / 2, eyeH / 2))
            ink.setFill(); e.fill()
            if blink > 0.55 {          // specular dot — the thing that makes eyes alive
                NSColor(white: 1, alpha: 0.92 * blink).setFill()
                NSBezierPath(ovalIn: NSRect(x: ex - eyeW * 0.08, y: ey - eyeH * 0.26,
                                            width: eyeW * 0.30, height: eyeW * 0.30)).fill()
            }
        }

        // blush
        NSColor(srgbRed: 1.0, green: 0.60, blue: 0.55, alpha: 0.22).setFill()
        for sx in [-bw * 0.31, bw * 0.31] {
            NSBezierPath(ovalIn: NSRect(x: cx + sx - bw * 0.075, y: eyeY + bh * 0.075,
                                        width: bw * 0.15, height: bh * 0.055)).fill()
        }

        // mouth: a smile that opens into speech
        let my = eyeY + bh * 0.155
        let mw = bw * (0.15 + 0.10 * mouth)
        let mh = bh * (0.020 + 0.115 * mouth)
        let m = NSBezierPath()
        m.move(to: NSPoint(x: cx - mw / 2, y: my))
        m.curve(to: NSPoint(x: cx + mw / 2, y: my),
                controlPoint1: NSPoint(x: cx - mw * 0.25, y: my + mh),
                controlPoint2: NSPoint(x: cx + mw * 0.25, y: my + mh))
        m.curve(to: NSPoint(x: cx - mw / 2, y: my),
                controlPoint1: NSPoint(x: cx + mw * 0.22, y: my - mh * 0.25),
                controlPoint2: NSPoint(x: cx - mw * 0.22, y: my - mh * 0.25))
        ink.setFill(); m.fill()

        // thinking: three dots that fill in turn
        if mood == .thinking {
            for i in 0..<3 {
                let on = (Int(t * 3) % 3) == i
                let d = bw * (0.048 + (on ? 0.020 : 0))
                NSColor(srgbRed: 0.62, green: 0.46, blue: 0.13,
                        alpha: on ? 0.95 : 0.38).setFill()
                NSBezierPath(ovalIn: NSRect(x: cx - bw * 0.10 + CGFloat(i) * bw * 0.095 - d / 2,
                                            y: ty - bh * 0.09 - d / 2,
                                            width: d, height: d)).fill()
            }
        }
    }

    /// A soft gumdrop: wide rounded dome, gently scalloped hem. Two curves a
    /// side instead of a polyline — the old shape had visible corners.
    private func blob(cx: CGFloat, left: CGFloat, top: CGFloat,
                      w: CGFloat, h: CGFloat) -> NSBezierPath {
        let right = left + w, bottom = top + h
        let shoulder = top + h * 0.46
        let p = NSBezierPath()
        p.move(to: NSPoint(x: left, y: bottom - h * 0.06))
        p.curve(to: NSPoint(x: cx, y: top),
                controlPoint1: NSPoint(x: left, y: shoulder - h * 0.30),
                controlPoint2: NSPoint(x: left + w * 0.16, y: top))
        p.curve(to: NSPoint(x: right, y: bottom - h * 0.06),
                controlPoint1: NSPoint(x: right - w * 0.16, y: top),
                controlPoint2: NSPoint(x: right, y: shoulder - h * 0.30))
        // hem: soft lobes that bulge DOWN with gentle cusps between them —
        // the previous version made one sharp central V, which read as a tooth
        let lobes = 3
        let seg = w / CGFloat(lobes)
        let dip = h * 0.055
        for i in 0..<lobes {
            let x1 = right - CGFloat(i + 1) * seg
            let x0 = right - CGFloat(i) * seg
            p.curve(to: NSPoint(x: x1, y: bottom - h * 0.06),
                    controlPoint1: NSPoint(x: x0 - seg * 0.22, y: bottom + dip),
                    controlPoint2: NSPoint(x: x1 + seg * 0.22, y: bottom + dip))
        }
        p.close()
        return p
    }
}

// MARK: - the window

final class CasperWindow: NSWindow {
    override var canBecomeKey: Bool { true }
}

final class App: NSObject, NSApplicationDelegate {
    var window: CasperWindow!
    var ghost: GhostView!
    var bubble: NSTextField!
    var yesBtn: NSButton!
    var noBtn: NSButton!
    var askBtn: NSButton!
    let synth = AVSpeechSynthesizer()

    var pendingAction = ""
    var lastSpoken = ""
    var busy = false

    func applicationDidFinishLaunching(_ n: Notification) {
        let W: CGFloat = 320, H: CGFloat = 360
        let screen = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        let frame = NSRect(x: screen.maxX - W - 24, y: screen.minY + 40, width: W, height: H)

        window = CasperWindow(contentRect: frame, styleMask: [.borderless],
                              backing: .buffered, defer: false)
        window.isOpaque = false
        window.backgroundColor = .clear
        window.level = .floating                    // above normal windows
        window.hasShadow = false
        window.isMovableByWindowBackground = true   // drag him anywhere
        window.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]

        let root = NSView(frame: NSRect(origin: .zero, size: frame.size))
        window.contentView = root

        ghost = GhostView(frame: NSRect(x: 55, y: 8, width: 210, height: 210))
        root.addSubview(ghost)

        bubble = NSTextField(wrappingLabelWithString: "")
        bubble.frame = NSRect(x: 20, y: 234, width: W - 40, height: 78)
        bubble.alignment = .center
        bubble.font = .systemFont(ofSize: 12.5, weight: .regular)
        bubble.textColor = NSColor(calibratedWhite: 0.88, alpha: 1)
        bubble.backgroundColor = NSColor(calibratedWhite: 0.09, alpha: 0.90)
        bubble.drawsBackground = true
        bubble.isBezeled = false
        bubble.isEditable = false
        bubble.wantsLayer = true
        bubble.layer?.cornerRadius = 14
        bubble.maximumNumberOfLines = 4
        bubble.lineBreakMode = .byTruncatingTail
        root.addSubview(bubble)

        yesBtn = button("Yes, fix it", x: 20)
        yesBtn.target = self; yesBtn.action = #selector(sayYes)
        yesBtn.keyEquivalent = "\r"                 // the obvious answer is the default
        yesBtn.bezelColor = NSColor(srgbRed: 0.91, green: 0.71, blue: 0.25, alpha: 1)
        noBtn = button("Not now", x: 122)
        noBtn.target = self; noBtn.action = #selector(sayNo)
        askBtn = button("Ask me…", x: 224)
        askBtn.target = self; askBtn.action = #selector(askSomething)
        [yesBtn, noBtn, askBtn].forEach { root.addSubview($0!) }
        showOffer(false)

        window.makeKeyAndOrderFront(nil)
        NSApp.setActivationPolicy(.accessory)       // menubar-less companion

        Timer.scheduledTimer(withTimeInterval: 1.0 / 60, repeats: true) { _ in
            // one place decides the mood, so the face never argues with itself
            if self.synth.isSpeaking            { self.ghost.mood = .speaking }
            else if self.busy                   { self.ghost.mood = .thinking }
            else if !self.yesBtn.isHidden       { self.ghost.mood = .listening }
            else                                { self.ghost.mood = .idle }
            self.ghost.tick(dt: 1.0 / 60)
        }
        Timer.scheduledTimer(withTimeInterval: 20, repeats: true) { _ in self.check() }
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) { self.check() }
    }

    func button(_ title: String, x: CGFloat) -> NSButton {
        let b = NSButton(title: title, target: nil, action: nil)
        b.frame = NSRect(x: x, y: 320, width: 90, height: 26)
        b.bezelStyle = .rounded
        b.font = .systemFont(ofSize: 11.5, weight: .medium)
        return b
    }

    func showOffer(_ on: Bool) {
        yesBtn.isHidden = !on; noBtn.isHidden = !on
        askBtn.isHidden = false
    }

    func say(_ text: String) {
        bubble.stringValue = text
        let u = AVSpeechUtterance(string: text)
        u.rate = 0.5
        u.voice = AVSpeechSynthesisVoice(identifier: "com.apple.voice.compact.en-US.Samantha")
            ?? AVSpeechSynthesisVoice(language: "en-US")
        synth.speak(u)
    }

    /// Poll meditate. If there's something worth saying AND you're at a pause,
    /// brighten, speak it once, and OFFER the fix as a question.
    func check() {
        DispatchQueue.global().async {
            guard let b = Meditate.brief() else { return }
            DispatchQueue.main.async {
                let hasSomething = !b.headline.isEmpty && b.kind != "clear"
                self.ghost.glow = hasSomething ? 1.0 : 0.25
                guard hasSomething, b.canInterrupt, b.headline != self.lastSpoken,
                      !self.synth.isSpeaking else { return }
                self.lastSpoken = b.headline
                self.pendingAction = b.action
                let offer = b.action.isEmpty ? "" :
                    "  Want me to \(b.action.contains("fix") ? "fix it" : "get someone on it")?"
                self.say(b.headline + offer)
                self.showOffer(!b.action.isEmpty)
            }
        }
    }

    @objc func sayYes() {
        let action = pendingAction
        showOffer(false)
        bubble.stringValue = "Running \(action)…"
        DispatchQueue.global().async {
            let out = Meditate.perform(action)
            let first = out.split(separator: "\n").first.map(String.init) ?? "Done."
            DispatchQueue.main.async { self.say(first) }
        }
    }

    @objc func sayNo() {
        showOffer(false)
        say("Alright — I'll leave it.")
    }

    /// Type a question; Casper reasons over the graded facts and answers.
    @objc func askSomething() {
        let alert = NSAlert()
        alert.messageText = "Ask Casper"
        alert.informativeText = "He answers from what he actually knows about your work."
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 300, height: 24))
        field.placeholderString = "what should I work on next?"
        alert.accessoryView = field
        alert.addButton(withTitle: "Ask")
        alert.addButton(withTitle: "Cancel")
        NSApp.activate(ignoringOtherApps: true)
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        let q = field.stringValue.trimmingCharacters(in: .whitespaces)
        guard q.count > 2 else { return }
        bubble.stringValue = "Thinking…"
        ghost.glow = 1.0
        DispatchQueue.global().async {
            let answer = Meditate.advise(q)
            DispatchQueue.main.async {
                self.say(answer.isEmpty ? "I'm not sure about that one." : answer)
            }
        }
    }
}

// MARK: - render mode
//
// `casper --render <dir>` draws frames to PNG and exits. This exists so his
// appearance can be REVIEWED instead of asserted — a mascot is the one part
// of this tool where "it compiles" proves nothing.

func renderFrames(to dir: String) {
    let v = GhostView(frame: NSRect(x: 0, y: 0, width: 260, height: 260))
    let moods: [(String, GhostView.Mood, CGFloat)] = [
        ("idle", .idle, 0.25), ("alert", .alert, 1.0),
        ("listening", .listening, 1.0), ("speaking", .speaking, 1.0),
        ("thinking", .thinking, 0.6), ("blink", .idle, 0.25)]
    for (name, mood, glow) in moods {
        v.mood = mood; v.glow = glow
        for _ in 0..<90 { v.tick(dt: 1.0 / 60) }
        if name == "blink" { v.forceBlink(); v.tick(dt: 1.0 / 60) }
        guard let rep = v.bitmapImageRepForCachingDisplay(in: v.bounds) else { continue }
        rep.size = v.bounds.size
        v.cacheDisplay(in: v.bounds, to: rep)
        if let png = rep.representation(using: .png, properties: [:]) {
            try? png.write(to: URL(fileURLWithPath: "\(dir)/casper-\(name).png"))
            print("wrote casper-\(name).png")
        }
    }
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
