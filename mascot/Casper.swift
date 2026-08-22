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

    static func brief() -> Brief? {
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

final class GhostView: NSView {
    var glow: CGFloat = 0.25          // 0.2 idle .. 1.0 has something to say
    var bob: CGFloat = 0
    var speaking = false

    override var isFlipped: Bool { true }

    override func draw(_ dirty: NSRect) {
        guard let ctx = NSGraphicsContext.current?.cgContext else { return }
        let w = bounds.width, h = bounds.height
        let cx = w / 2
        let top = 14 + bob

        // soft aura
        ctx.saveGState()
        let aura = NSBezierPath(ovalIn: NSRect(x: cx - w * 0.42, y: top - 6,
                                               width: w * 0.84, height: h * 0.78))
        NSColor(calibratedRed: 0.89, green: 0.69, blue: 0.25, alpha: 0.10 * glow).setFill()
        aura.fill()
        ctx.restoreGState()

        // body: dome + wavy skirt
        let bodyW = w * 0.62, bodyH = h * 0.66
        let left = cx - bodyW / 2
        let path = NSBezierPath()
        path.move(to: NSPoint(x: left, y: top + bodyH))
        path.line(to: NSPoint(x: left, y: top + bodyW * 0.5))
        path.appendArc(withCenter: NSPoint(x: cx, y: top + bodyW * 0.5),
                       radius: bodyW / 2, startAngle: 180, endAngle: 0, clockwise: true)
        path.line(to: NSPoint(x: left + bodyW, y: top + bodyH))
        // three soft waves along the hem
        let waves = 3
        let seg = bodyW / CGFloat(waves)
        for i in 0..<waves {
            let x0 = left + bodyW - CGFloat(i) * seg
            path.curve(to: NSPoint(x: x0 - seg, y: top + bodyH),
                       controlPoint1: NSPoint(x: x0 - seg * 0.25, y: top + bodyH - 11),
                       controlPoint2: NSPoint(x: x0 - seg * 0.75, y: top + bodyH - 11))
        }
        path.close()

        let grad = NSGradient(colors: [
            NSColor(calibratedRed: 1.00, green: 0.96, blue: 0.87, alpha: 0.55 + 0.4 * glow),
            NSColor(calibratedRed: 0.89, green: 0.69, blue: 0.25, alpha: 0.55 + 0.4 * glow),
            NSColor(calibratedRed: 0.49, green: 0.35, blue: 0.07, alpha: 0.55 + 0.4 * glow)])
        grad?.draw(in: path, angle: -70)

        // eyes + mouth
        let eyeY = top + bodyW * 0.42
        let eyeR: CGFloat = 4.6
        NSColor(calibratedWhite: 0.07, alpha: 0.9).setFill()
        NSBezierPath(ovalIn: NSRect(x: cx - bodyW * 0.20 - eyeR, y: eyeY - 6,
                                    width: eyeR * 2, height: 12)).fill()
        NSBezierPath(ovalIn: NSRect(x: cx + bodyW * 0.20 - eyeR, y: eyeY - 6,
                                    width: eyeR * 2, height: 12)).fill()
        let mh: CGFloat = speaking ? CGFloat.random(in: 4...11) : 5
        NSBezierPath(ovalIn: NSRect(x: cx - 6, y: eyeY + 17, width: 12, height: mh)).fill()
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
    var t: CGFloat = 0

    func applicationDidFinishLaunching(_ n: Notification) {
        let W: CGFloat = 300, H: CGFloat = 330
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

        ghost = GhostView(frame: NSRect(x: 60, y: 120, width: 180, height: 200))
        root.addSubview(ghost)

        bubble = NSTextField(wrappingLabelWithString: "")
        bubble.frame = NSRect(x: 8, y: 8, width: W - 16, height: 104)
        bubble.alignment = .center
        bubble.font = .systemFont(ofSize: 12.5)
        bubble.textColor = NSColor(calibratedWhite: 0.88, alpha: 1)
        bubble.backgroundColor = NSColor(calibratedWhite: 0.08, alpha: 0.86)
        bubble.drawsBackground = true
        bubble.isBezeled = false
        bubble.isEditable = false
        bubble.wantsLayer = true
        bubble.layer?.cornerRadius = 12
        root.addSubview(bubble)

        yesBtn = button("Yes, do it", x: 8)
        yesBtn.target = self; yesBtn.action = #selector(sayYes)
        noBtn = button("Not now", x: 108)
        noBtn.target = self; noBtn.action = #selector(sayNo)
        askBtn = button("Ask…", x: 200)
        askBtn.target = self; askBtn.action = #selector(askSomething)
        [yesBtn, noBtn, askBtn].forEach { root.addSubview($0!) }
        showOffer(false)

        window.makeKeyAndOrderFront(nil)
        NSApp.setActivationPolicy(.accessory)       // menubar-less companion

        Timer.scheduledTimer(withTimeInterval: 1.0 / 20, repeats: true) { _ in
            self.t += 0.05
            self.ghost.bob = sin(self.t) * 5
            self.ghost.speaking = self.synth.isSpeaking
            self.ghost.needsDisplay = true
        }
        Timer.scheduledTimer(withTimeInterval: 20, repeats: true) { _ in self.check() }
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) { self.check() }
    }

    func button(_ title: String, x: CGFloat) -> NSButton {
        let b = NSButton(title: title, target: nil, action: nil)
        b.frame = NSRect(x: x, y: 116, width: 92, height: 24)
        b.bezelStyle = .rounded
        b.font = .systemFont(ofSize: 11)
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

let app = NSApplication.shared
let delegate = App()
app.delegate = delegate
app.run()
