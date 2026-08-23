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
import Speech

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

/// Was this said TO him, and if so what is the question?
///
/// Kept out of the view controller on purpose: this is the rule that decides
/// whether a desktop companion speaks over your meeting or not, and a rule
/// that can only be exercised by talking at a live window is a rule nobody
/// checks. Returns nil when the utterance was not aimed at him.
func addressedQuestion(_ raw: String, armed: Bool) -> String? {
    let text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
    let lower = text.lowercased()
    let names = ["hey casper", "ok casper", "okay casper", "casper", "jasper"]
    var addressed = armed
    var question = text
    for n in names where lower.hasPrefix(n) {
        addressed = true
        question = String(text.dropFirst(n.count))
            .trimmingCharacters(in: CharacterSet(charactersIn: " ,.?!"))
        break
    }
    if !addressed && lower.contains("casper") { addressed = true }
    guard addressed, question.count > 2 else { return nil }
    return question
}


// MARK: - the ghost
//
// Everything that moves lives in tick(); draw() only draws. That split is the
// whole difference between a mascot that looks alive and one that looks
// broken — the previous mouth picked a new random height every frame, which
// reads as flicker, not speech.

final class GhostView: NSView {
    enum Mood { case idle, listening, thinking, speaking, alert }
    /// A short-lived colour over the top of the mood. Moods say what he is
    /// DOING; feelings say how it went, and fade on their own.
    enum Feeling { case neutral, happy, surprised, sleepy }

    var mood: Mood = .idle
    var glow: CGFloat = 0.25          // 0.25 calm .. 1.0 has something to say
    private(set) var feeling: Feeling = .neutral
    private var feelingUntil: CGFloat = 0

    // smoothed animation state — every one of these eases, none of them jump
    private var t: CGFloat = 0
    private var blink: CGFloat = 1            // 1 open .. 0 shut
    private var blinkAt: CGFloat = 2.5
    private var blinksOwed = 0                // a double-blink reads as noticing
    private var mouth: CGFloat = 0            // 0 closed .. 1 wide
    private var mouthTarget: CGFloat = 0
    private var gaze = CGPoint(x: 0, y: 0)
    private var gazeTo = CGPoint(x: 0, y: 0)
    private var gazeAt: CGFloat = 1.5
    private var lean: CGFloat = 0
    private var ring: CGFloat = 0

    // ---- body physics: one spring, doing all the bouncing ------------------
    // A hop, a landing squash and a click bounce are the same thing from a
    // mascot's point of view: a mass on a spring. Driving them from real
    // physics is why the squash lands on the beat instead of near it.
    private var hop: CGFloat = 0              // vertical offset, + is down
    private var hopV: CGFloat = 0
    private var tilt: CGFloat = 0             // head tilt, radians
    private var tiltTo: CGFloat = 0
    private var sway: CGFloat = 0             // side to side

    // ---- idle life ---------------------------------------------------------
    private enum Act { case none, look, tilt, hop, yawn, wiggle, peek }
    private var act: Act = .none
    private var actUntil: CGFloat = 0
    private var nextActAt: CGFloat = 3

    /// How lively he is right now, 0..1. Rises when things are happening,
    /// decays toward calm when they are not — so he is bouncy in a busy
    /// moment and quietly breathing at 2am, without anyone scripting it.
    private(set) var energy: CGFloat = 0.45

    // ---- sparkles ----------------------------------------------------------
    private struct Spark { var x, y, vx, vy, life: CGFloat }
    private var sparks: [Spark] = []

    // ---- arriving ----------------------------------------------------------
    /// 0 = not here yet, 1 = fully present. Only ever below 1 on the very
    /// first run, when he gathers himself out of nothing in front of you.
    private var appear: CGFloat = 1
    private var burstDone = true

    /// Live microphone loudness, 0..1, set by the Ear. The listening pose is
    /// driven by this and nothing else — he leans further and his sound arcs
    /// swell because you actually got louder.
    var hearLevel: CGFloat = 0
    /// Amplitude of the audio playing this instant, set by the Mouth.
    /// Negative means "no real signal", and only then does the fallback
    /// oscillator run.
    var mouthDrive: CGFloat = -1

    // ---- adaptive redraw ---------------------------------------------------
    // Counted, not assumed: a mascot that repaints 60 times a second to show
    // nothing moving is a battery bug with a face.
    private(set) var framesSeen = 0
    private(set) var framesDrawn = 0
    private var quietFrames = 0

    override var isFlipped: Bool { true }

    /// Force the next frame to be mid-blink. Only render mode uses this —
    /// blinks are on a random schedule, so a review frame would never catch one.
    func forceBlink() { blinkAt = t - 0.001; blink = 0.18 }

    // ---- things the app can ask him to do ----------------------------------

    /// Poked. A quick squash and a look up at you.
    func bounce() {
        hopV -= 190
        blinksOwed = 1
        gazeTo = CGPoint(x: 0, y: -0.35)
        gazeAt = t + 1.2
        energy = min(1, energy + 0.35)
    }

    /// Something went well. This is the one that makes people smile back.
    func celebrate() {
        feel(.happy, for: 2.6)
        hopV -= 300
        energy = min(1, energy + 0.5)
        for i in 0..<14 {
            let a = CGFloat(i) / 14 * .pi * 2 + CGFloat.random(in: -0.2...0.2)
            sparks.append(Spark(x: bounds.width / 2 + cos(a) * 14,
                                y: bounds.height * 0.42 + sin(a) * 14,
                                vx: cos(a) * CGFloat.random(in: 40...95),
                                vy: sin(a) * CGFloat.random(in: 40...95) - 40,
                                life: CGFloat.random(in: 0.7...1.3)))
        }
    }

    /// Arrive. Sparks rush INWARD and gather, he swells out of nothing with
    /// an overshoot, then they scatter and his eyes open on you.
    ///
    /// Only the first run gets this. A trick you have seen twice is furniture.
    func materialize() {
        appear = 0
        burstDone = false
        blink = 0
        blinkAt = 1e9                 // eyes stay shut until he is here
        sparks.removeAll()
        let cx = bounds.width / 2, cy = bounds.height * 0.42
        for i in 0..<20 {
            let a = CGFloat(i) / 20 * .pi * 2
            let r = CGFloat.random(in: 62...96)
            sparks.append(Spark(x: cx + cos(a) * r, y: cy + sin(a) * r,
                                vx: -cos(a) * r * 1.5, vy: -sin(a) * r * 1.5,
                                life: 0.85))
        }
    }

    /// Caught off guard.
    func startle() { feel(.surprised, for: 1.1); hopV -= 90; blinksOwed = 0 }

    func feel(_ f: Feeling, for seconds: CGFloat) {
        feeling = f
        feelingUntil = t + seconds
    }

    /// One frame of life. Called at 60Hz.
    func tick(dt: CGFloat) {
        t += dt
        framesSeen += 1

        if t > feelingUntil { feeling = .neutral }

        // ---- arriving --------------------------------------------------------
        if appear < 1 {
            appear = min(1, appear + dt / 1.5)
            if !burstDone && appear > 0.52 {
                burstDone = true
                blink = 0
                blinkAt = t + 0.05        // and then he looks at you
                hopV -= 120
                let cx = bounds.width / 2, cy = bounds.height * 0.42
                for i in 0..<16 {
                    let a = CGFloat(i) / 16 * .pi * 2 + 0.2
                    sparks.append(Spark(x: cx + cos(a) * 8, y: cy + sin(a) * 8,
                                        vx: cos(a) * CGFloat.random(in: 55...120),
                                        vy: sin(a) * CGFloat.random(in: 55...120) - 30,
                                        life: CGFloat.random(in: 0.6...1.2)))
                }
            }
            if appear >= 1 { feel(.happy, for: 2.4) }
        }

        // ---- energy: what is actually happening, not a script --------------
        let busy: CGFloat = (mood == .speaking || mood == .listening) ? 0.95
                          : (mood == .alert || mood == .thinking) ? 0.8
                          : 0.18
        energy += (busy - energy) * min(1, dt * 0.35)

        // ---- blink ----------------------------------------------------------
        if t > blinkAt {
            blink -= dt * 14
            if blink <= 0 {
                blink = 0
                if blinksOwed > 0 { blinksOwed -= 1; blinkAt = t + 0.16 }
                else { blinkAt = t + CGFloat.random(in: 2.2...5.5) }
            }
        } else if blink < 1 {
            blink = min(1, blink + dt * 9)
        }

        // ---- idle life: he does small things when nobody is asking ---------
        // Without this he is a screensaver. The gap between acts shortens as
        // energy rises, so he is fidgety when busy and still when calm.
        if mood == .idle && act == .none && t > nextActAt {
            act = pickAct()
            actUntil = t + (act == .yawn ? 1.9 : 1.2)
            switch act {
            case .look:
                gazeTo = CGPoint(x: .random(in: -0.9 ... 0.9), y: .random(in: -0.7 ... 0.4))
                gazeAt = t + 2.5
            case .tilt:   tiltTo = CGFloat.random(in: -0.16 ... 0.16)
            case .hop:    hopV -= 150
            case .peek:   gazeTo = CGPoint(x: 0, y: -0.45); gazeAt = t + 2; blinksOwed = 1
            case .yawn:   feel(.sleepy, for: 2.2)
            default:      break
            }
        }
        if act != .none && t > actUntil {
            act = .none
            tiltTo = 0
            // livelier now = sooner again
            nextActAt = t + CGFloat.random(in: 3.5...9.0) * (1.25 - energy * 0.7)
        }

        // ---- gaze -----------------------------------------------------------
        if mood == .thinking {
            gazeTo = CGPoint(x: -0.5, y: -0.6)
        } else if mood == .listening {
            gazeTo = CGPoint(x: 0, y: -0.25)          // look AT you while you talk
        } else if t > gazeAt {
            gazeTo = CGPoint(x: .random(in: -0.45...0.45), y: .random(in: -0.3...0.25))
            gazeAt = t + CGFloat.random(in: 1.4...3.6)
        }
        gaze.x += (gazeTo.x - gaze.x) * min(1, dt * 6)
        gaze.y += (gazeTo.y - gaze.y) * min(1, dt * 6)

        // ---- mouth -----------------------------------------------------------
        if mood == .speaking {
            if mouthDrive >= 0 {
                mouthTarget = 0.12 + 0.88 * mouthDrive     // the real waveform
            } else {
                let syl = (sin(t * 11.5) * 0.5 + 0.5) * (sin(t * 4.3) * 0.35 + 0.65)
                mouthTarget = 0.25 + 0.75 * syl            // only if audio is unavailable
            }
        } else if act == .yawn {
            let u = 1 - abs((t - (actUntil - 1.9)) / 1.9 * 2 - 1)   // in and out
            mouthTarget = max(0, u) * 0.85
        } else if mood == .listening {
            mouthTarget = 0.06 + 0.10 * hearLevel
        } else if feeling == .happy {
            mouthTarget = 0.22
        } else {
            mouthTarget = 0
        }
        mouth += (mouthTarget - mouth) * min(1, dt * 16)

        // ---- spring, tilt, sway ----------------------------------------------
        hopV += (-hop * 220 - hopV * 9) * dt          // pull home, damped
        hop  += hopV * dt
        tilt += (tiltTo - tilt) * min(1, dt * 6)
        let swayTo: CGFloat = (act == .wiggle) ? sin(t * 7) * 4.5 : 0
        sway += (swayTo - sway) * min(1, dt * 8)

        // ---- lean and the listening arcs --------------------------------------
        let leanTo: CGFloat = (mood == .listening) ? (0.45 + 0.55 * hearLevel) : 0
        lean += (leanTo - lean) * min(1, dt * 5)
        if mood == .listening {
            ring = max(0.22, min(1, hearLevel * 1.35))
        } else if mood == .alert {
            ring = 0.62 + 0.38 * sin(t * 2.4)
        } else {
            ring = 0
        }

        // ---- sparkles ----------------------------------------------------------
        if !sparks.isEmpty {
            for i in sparks.indices {
                sparks[i].x += sparks[i].vx * dt
                sparks[i].y += sparks[i].vy * dt
                if burstDone { sparks[i].vy += 150 * dt }   // gravity, after he lands
                sparks[i].life -= dt
            }
            sparks.removeAll { $0.life <= 0 }
        }

        // ---- adaptive redraw ---------------------------------------------------
        let moving = appear < 1 || mood != .idle || act != .none || !sparks.isEmpty
            || abs(hopV) > 1 || abs(hop) > 0.3 || mouth > 0.01
            || blink < 0.999 || feeling != .neutral
        if moving {
            quietFrames = 0
        } else {
            quietFrames += 1
        }
        // still breathing when calm, just repainted a third as often
        if moving || quietFrames % 3 == 0 {
            framesDrawn += 1
            needsDisplay = true
        }
    }

    private func pickAct() -> Act {
        // low energy leans sleepy and small; high energy leans bouncy
        var bag: [Act] = [.look, .look, .tilt, .peek]
        if energy > 0.5 { bag += [.hop, .wiggle, .look] }
        if energy < 0.35 { bag += [.yawn, .tilt] }
        return bag.randomElement() ?? .look
    }

    // palette: warm off-white body so he reads on any wallpaper, amber accent
    private let bodyTop = NSColor(srgbRed: 1.00, green: 0.99, blue: 0.97, alpha: 1)
    private let bodyBot = NSColor(srgbRed: 0.90, green: 0.87, blue: 0.83, alpha: 1)
    private let ink     = NSColor(srgbRed: 0.16, green: 0.16, blue: 0.20, alpha: 1)
    private let amber   = NSColor(srgbRed: 0.91, green: 0.71, blue: 0.25, alpha: 1)

    var onClick: (() -> Void)?
    private var downAt = NSPoint.zero

    override func mouseDown(with e: NSEvent) { downAt = e.locationInWindow }
    override func mouseUp(with e: NSEvent) {
        let d = hypot(e.locationInWindow.x - downAt.x, e.locationInWindow.y - downAt.y)
        if d < 4 { bounce(); onClick?() } else { super.mouseUp(with: e) }
    }

    override func draw(_ dirty: NSRect) {
        guard let ctx = NSGraphicsContext.current?.cgContext else { return }
        let w = bounds.width, h = bounds.height
        let cx = w / 2 + sway

        // breathing: squash and stretch that preserves volume, so he never
        // looks like he is simply scaling up and down. The spring adds its own
        // on top — stretched going up, squashed on the landing.
        let breath = sin(t * 1.9) * 0.022
        let springSquash = max(-0.12, min(0.12, hopV * 0.00055))
        let bob = sin(t * 1.9) * 3.5 - lean * 4 + hop
        var bw = w * 0.66 * (1 - breath + springSquash)
        var bh = h * 0.70 * (1 + breath - springSquash)
        bw *= 1 + glow * 0.02; bh *= 1 + glow * 0.02

        // arriving: swell out of nothing with an overshoot, so he lands rather
        // than fades up. A linear scale reads as a slow render, not an entrance.
        if appear < 1 {
            // squared first, so he starts genuinely tiny. Feeding `appear`
            // straight into easeOutBack put him at 61% size a quarter second
            // in, which looks like a slow paint rather than an arrival.
            let x = appear * appear
            let c1: CGFloat = 1.9, c3 = c1 + 1
            let pop = max(0.02, 1 + c3 * pow(x - 1, 3) + c1 * pow(x - 1, 2))
            bw *= pop; bh *= pop
        }
        let ty = h * 0.10 + bob
        let left = cx - bw / 2

        // ---- ground shadow: what makes him sit in the world, not float on it
        // Drawn BEFORE the tilt, and shrinking as he rises — a shadow that
        // leans with the body is the thing that gives away a fake hop.
        ctx.saveGState()
        let lift = max(0, -hop) / max(1, bh) * 0.9
        let shW = bw * 0.54 * (1 - lift * 0.45)
        let sh = NSBezierPath(ovalIn: NSRect(x: w / 2 - shW / 2,
                                             y: h * 0.10 + bh + 3,
                                             width: shW, height: bh * 0.052))
        NSColor(white: 0, alpha: 0.10 * (1 - lift * 0.5)).setFill()
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

        // everything from here tilts together, pivoting on where he meets the
        // ground — tilting about the centre makes a head look detached
        ctx.saveGState()
        if appear < 1 { ctx.setAlpha(min(1, pow(appear, 0.7) * 1.5)) }
        if abs(tilt) > 0.0005 {
            ctx.translateBy(x: cx, y: ty + bh)
            ctx.rotate(by: tilt)
            ctx.translateBy(x: -cx, y: -(ty + bh))
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
        drawEyes(cx: cx, eyeY: eyeY, eyeDX: eyeDX, bw: bw, bh: bh)

        // blush — deeper when pleased, which is most of what "cute" is
        let blushA: CGFloat = feeling == .happy ? 0.42 : 0.22
        NSColor(srgbRed: 1.0, green: 0.60, blue: 0.55, alpha: blushA).setFill()
        let blushW: CGFloat = feeling == .happy ? 0.175 : 0.15
        for sx in [-bw * 0.31, bw * 0.31] {
            NSBezierPath(ovalIn: NSRect(x: cx + sx - bw * blushW / 2,
                                        y: eyeY + bh * 0.075,
                                        width: bw * blushW, height: bh * 0.055)).fill()
        }

        // mouth: a smile that opens into speech
        let my = eyeY + bh * 0.155
        let mw = bw * (0.15 + 0.10 * mouth) * (feeling == .happy ? 1.35 : 1)
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

        // sleepy: one little z drifting up
        if feeling == .sleepy {
            let u = (t * 0.6).truncatingRemainder(dividingBy: 1)
            NSColor(srgbRed: 0.62, green: 0.46, blue: 0.13,
                    alpha: 0.8 * (1 - u)).setFill()
            let z = NSAttributedString(string: "z", attributes: [
                .font: NSFont.systemFont(ofSize: bw * 0.20, weight: .bold),
                .foregroundColor: NSColor(srgbRed: 0.55, green: 0.40, blue: 0.10,
                                          alpha: 0.85 * (1 - u * 0.75))])
            z.draw(at: NSPoint(x: cx + bw * 0.26 + u * 6, y: ty - bh * 0.02 - u * 16))
        }

        ctx.restoreGState()      // end tilt

        // ---- sparkles, on top of everything and untilted
        for s in sparks {
            let a = max(0, min(1, s.life))
            amber.withAlphaComponent(a).setFill()
            let d = 3.2 * a + 1.2
            NSBezierPath(ovalIn: NSRect(x: s.x - d / 2, y: s.y - d / 2,
                                        width: d, height: d)).fill()
        }
    }

    /// Eyes carry the whole expression, so they get their own routine.
    private func drawEyes(cx: CGFloat, eyeY: CGFloat, eyeDX: CGFloat,
                          bw: CGFloat, bh: CGFloat) {
        let openAmt = max(0.08, blink)
        let wide: CGFloat = feeling == .surprised ? 1.45 : 1.0
        let lid: CGFloat = feeling == .sleepy ? 0.45 : 1.0
        let eyeW = bw * 0.115 * wide
        let eyeH = eyeW * 1.45 * openAmt * lid

        for sx in [-eyeDX, eyeDX] {
            let ex = cx + sx + gaze.x * bw * 0.055
            let ey = eyeY + gaze.y * bh * 0.042

            // sleepy: heavy lids drawn as a downward curve. Squashing the
            // capsule was not enough — a short wide oval with a highlight in
            // it still reads wide awake, which is the opposite of the point.
            if feeling == .sleepy && blink > 0.6 {
                let a = NSBezierPath()
                let r = eyeW * 0.95
                a.move(to: NSPoint(x: ex - r, y: ey - r * 0.15))
                a.curve(to: NSPoint(x: ex + r, y: ey - r * 0.15),
                        controlPoint1: NSPoint(x: ex - r * 0.35, y: ey + r * 0.55),
                        controlPoint2: NSPoint(x: ex + r * 0.35, y: ey + r * 0.55))
                a.lineWidth = max(1.6, eyeW * 0.34)
                a.lineCapStyle = .round
                ink.withAlphaComponent(0.85).setStroke(); a.stroke()
                continue
            }

            // happy eyes are arcs, not dots — the single strongest "cute" cue
            // there is, and it costs one bezier
            if feeling == .happy && blink > 0.6 {
                let a = NSBezierPath()
                let r = eyeW * 0.95
                a.move(to: NSPoint(x: ex - r, y: ey + r * 0.35))
                a.curve(to: NSPoint(x: ex + r, y: ey + r * 0.35),
                        controlPoint1: NSPoint(x: ex - r * 0.35, y: ey - r * 0.75),
                        controlPoint2: NSPoint(x: ex + r * 0.35, y: ey - r * 0.75))
                a.lineWidth = max(1.8, eyeW * 0.42)
                a.lineCapStyle = .round
                ink.setStroke(); a.stroke()
                continue
            }

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
    var bubbleBox: NSView!
    var yesBtn: NSButton!
    var noBtn: NSButton!
    var askBtn: NSButton!
    var micBtn: NSButton!
    let mouth = Mouth()
    let ear = Ear()
    var armed = false          // click him = one turn without his name
    var earStatus = ""
    var serverState = "?"

    var pendingAction = ""
    var lastSpoken = ""
    var busy = false

    func applicationDidFinishLaunching(_ n: Notification) {
        let W: CGFloat = 200, H: CGFloat = 248
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

        ghost = GhostView(frame: NSRect(x: 34, y: 108, width: 132, height: 132))
        // Layer-backed, or he flickers. A borderless transparent window that
        // redraws 60 times a second without a layer lets the window server
        // composite half-drawn frames — the whole mascot strobes.
        root.wantsLayer = true
        ghost.wantsLayer = true
        ghost.canDrawSubviewsIntoLayer = true
        ghost.layerContentsRedrawPolicy = .onSetNeedsDisplay
        root.addSubview(ghost)

        // A dark card behind a transparent label, because NSTextField has no
        // padding of its own and text jammed against the edge of a box reads
        // as broken rather than minimal.
        bubbleBox = NSView(frame: NSRect(x: 6, y: 40, width: W - 12, height: 62))
        bubbleBox.wantsLayer = true
        bubbleBox.layer?.cornerRadius = 10
        bubbleBox.layer?.backgroundColor =
            NSColor(calibratedWhite: 0.13, alpha: 0.92).cgColor
        root.addSubview(bubbleBox)

        bubble = NSTextField(wrappingLabelWithString: "")
        bubble.frame = NSRect(x: 10, y: 7, width: W - 32, height: 48)
        bubble.alignment = .center
        bubble.font = .systemFont(ofSize: 11, weight: .regular)
        bubble.textColor = NSColor(calibratedWhite: 0.92, alpha: 1)
        bubble.drawsBackground = false
        bubble.isBezeled = false
        bubble.isEditable = false
        bubble.isSelectable = false
        bubble.maximumNumberOfLines = 4
        bubble.lineBreakMode = .byWordWrapping     // truncating hid the message
        bubble.cell?.wraps = true
        bubble.cell?.isScrollable = false
        bubbleBox.addSubview(bubble)

        yesBtn = button("Yes, fix it", x: 6, w: 92)
        yesBtn.target = self; yesBtn.action = #selector(sayYes)
        yesBtn.keyEquivalent = "\r"                 // the obvious answer is the default
        style(yesBtn, title: "Yes, fix it", accent: true)
        noBtn = button("Not now", x: 102, w: 92)
        noBtn.target = self; noBtn.action = #selector(sayNo)
        askBtn = button("Ask me", x: 102, w: 92)
        askBtn.target = self; askBtn.action = #selector(askSomething)
        micBtn = button("Talk to me", x: 6, w: 92)
        micBtn.target = self; micBtn.action = #selector(toggleMic)
        [yesBtn, noBtn, askBtn, micBtn].forEach { root.addSubview($0!) }
        showOffer(false)
        refreshMicButton()
        setBubble("")

        window.makeKeyAndOrderFront(nil)
        NSApp.setActivationPolicy(.accessory)       // menubar-less companion

        Timer.scheduledTimer(withTimeInterval: 1.0 / 60, repeats: true) { _ in
            // one place decides the mood, so the face never argues with itself
            // the two signals the face is allowed to animate from
            self.ghost.hearLevel = self.ear.level
            self.ghost.mouthDrive = self.mouth.speaking ? self.mouth.drive : -1

            if self.mouth.speaking                    { self.ghost.mood = .speaking }
            else if self.busy                         { self.ghost.mood = .thinking }
            else if self.ear.running && (self.armed || self.ear.level > self.ear.noiseFloor * 2.2) {
                self.ghost.mood = .listening }
            else if !self.yesBtn.isHidden             { self.ghost.mood = .alert }
            else                                      { self.ghost.mood = .idle }
            self.ghost.tick(dt: 1.0 / 60)
        }
        // ---- the ear -------------------------------------------------------
        // He listens all the time but only ANSWERS when addressed. Anything
        // else and a companion turns into a thing that talks over your calls.
        mouth.onFinish = { [weak self] in
            guard let self = self else { return }
            self.ear.muted = false            // safe to hear you again
        }
        ear.onPartial = { [weak self] text in
            guard let self = self, !text.isEmpty else { return }
            // Only echo speech that is aimed at him. He sits on the desktop
            // while you talk to other people all day; narrating every stray
            // fragment back at you turns a companion into a caption track.
            let mine = self.armed || text.lowercased().contains("casper")
            guard mine else { return }
            self.setBubble("\u{201C}" + text + "\u{201D}")
        }
        ear.onUtterance = { [weak self] text in self?.heard(text) }

        ghost.onClick = { [weak self] in self?.armForOneTurn() }

        if micEnabled {
            Ear.requestAccess { [weak self] ok, why in
                guard let self = self else { return }
                self.earStatus = ok ? "listening" : why
                if ok {
                    self.ear.start()
                    if !self.ear.running { self.earStatus = self.ear.lastError }
                }
                self.refreshMicButton()
                self.writeStatus()
            }
        } else {
            earStatus = "off — mic switched off"
            writeStatus()
        }
        // A GUI app launched with `open` has nowhere to print. One status line
        // on disk is how the ear can be checked without asking the owner what
        // he sees on his own screen.
        Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { _ in self.writeStatus() }

        Timer.scheduledTimer(withTimeInterval: 20, repeats: true) { _ in self.check() }
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) { self.greet() }
    }

    /// A button that draws itself.
    ///
    /// The stock `.rounded` bezel is a vibrancy material: on a fully
    /// transparent, borderless window it composites to nothing at all. The
    /// controls were present the whole time — frames right, isHidden false —
    /// and simply painted no pixels. Explicit fill, explicit text, no material.
    func button(_ title: String, x: CGFloat, w: CGFloat = 90) -> NSButton {
        let b = NSButton(title: title, target: nil, action: nil)
        b.frame = NSRect(x: x, y: 8, width: w, height: 26)
        b.isBordered = false
        b.wantsLayer = true
        b.layer?.cornerRadius = 8
        b.layer?.backgroundColor = NSColor(calibratedWhite: 0.13, alpha: 0.92).cgColor
        style(b, title: title, accent: false)
        return b
    }

    func style(_ b: NSButton, title: String, accent: Bool) {
        let fg = accent ? NSColor(calibratedWhite: 0.10, alpha: 1)
                        : NSColor(calibratedWhite: 0.93, alpha: 1)
        b.attributedTitle = NSAttributedString(string: title, attributes: [
            .foregroundColor: fg,
            .font: NSFont.systemFont(ofSize: 11.5, weight: .medium),
        ])
        b.layer?.backgroundColor = accent
            ? NSColor(srgbRed: 0.91, green: 0.71, blue: 0.25, alpha: 1).cgColor
            : NSColor(calibratedWhite: 0.13, alpha: 0.92).cgColor
    }

    /// An empty bubble is a grey slab taking up half the window. Hide it, and
    /// when there is nothing to say he is just a small ghost on your desktop.
    func setBubble(_ text: String) {
        bubble.stringValue = text
        bubbleBox.isHidden = text.isEmpty
        guard !text.isEmpty else { return }
        // grow the card to the text, up to four lines, and keep it centred
        let w = bubbleBox.frame.width - 20
        let h = min(64, max(20, bubble.sizeThatFits(
            NSSize(width: w, height: .greatestFiniteMagnitude)).height))
        bubble.frame = NSRect(x: 10, y: (h + 14 - h) / 2, width: w, height: h)
        bubbleBox.frame = NSRect(x: 6, y: 40, width: bubbleBox.frame.width,
                                 height: h + 14)
        bubble.frame.origin.y = 7
    }

    func showOffer(_ on: Bool) {
        yesBtn.isHidden = !on
        noBtn.isHidden = !on
        askBtn.isHidden = on          // one row, two states — never four buttons
        micBtn.isHidden = on
    }

    func say(_ text: String) {
        setBubble(text)
        ear.muted = true                 // never answer his own last sentence
        mouth.say(text)
    }

    /// The first thing he ever says. Spoken whatever the timing layer thinks,
    /// because a companion that has never introduced itself is a widget, and
    /// because the rule that waits for a pause had him silent all day — anyone
    /// working at a keyboard is never idle long enough for it to fire.
    func greet() {
        var line = "Hi, I'm Casper. I keep an eye on your work. "
                 + "Click me any time and just talk."
        if let b = Meditate.brief(), !b.headline.isEmpty, b.kind != "clear" {
            line += " Right now: " + b.headline
            pendingAction = b.action
        }
        say(line)
        showOffer(!pendingAction.isEmpty)
        DispatchQueue.main.asyncAfter(deadline: .now() + 6) { self.check() }
    }

    /// Poll meditate. If there's something worth saying AND you're at a pause,
    /// brighten, speak it once, and OFFER the fix as a question.
    func check() {
        DispatchQueue.global().async {
            let viaServer = Meditate.briefFromServer()
            DispatchQueue.main.async { self.serverState = viaServer != nil ? "up" : "down" }
            guard let b = viaServer ?? Meditate.brief() else { return }
            DispatchQueue.main.async {
                let hasSomething = !b.headline.isEmpty && b.kind != "clear"
                self.ghost.glow = hasSomething ? 1.0 : 0.25
                guard hasSomething, b.canInterrupt, b.headline != self.lastSpoken,
                      !self.mouth.speaking else { return }
                self.lastSpoken = b.headline
                self.pendingAction = b.action
                self.ghost.startle()      // notice it before saying it
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
        setBubble("On it…")
        DispatchQueue.global().async {
            let out = Meditate.perform(action)
            let first = out.split(separator: "\n").first.map(String.init) ?? "Done."
            DispatchQueue.main.async {
                // The one unprompted celebration he gets: a job you asked for,
                // finished. Tying it to anything cheaper makes it worthless.
                let failed = first.lowercased().contains("error")
                    || first.lowercased().contains("nothing to run")
                if !failed { self.ghost.celebrate() }
                self.say(first)
            }
        }
    }

    /// Click him and he stays open until you click again.
    ///
    /// This was a 12-second window, which meant racing a timer you cannot see
    /// — click, think, start talking, and it has already closed on you. A
    /// toggle you can look at is the difference between talking to him and
    /// performing for him.
    func armForOneTurn() {
        if mouth.speaking { mouth.shutUp(); ear.muted = false; return }
        if armed {
            armed = false
            setBubble("Stopped listening. Click me when you want to talk.")
            return
        }
        guard ear.running else {
            if !micEnabled {
                setBubble("My microphone is off. Turn it on below when you "
                          + "want me to listen.")
                return
            }
            // A companion that is silently deaf is worse than one that says so
            // and shows you the switch. macOS only ever asks once, so being
            // denied is a dead end unless he offers the way out.
            if earStatus.contains("not authorised") {
                say("I can't hear you — microphone access is off. "
                    + "I'll open the setting; switch Casper on and click me again.")
                let pane = earStatus.contains("speech")
                    ? "Privacy_SpeechRecognition" : "Privacy_Microphone"
                if let u = URL(string:
                    "x-apple.systempreferences:com.apple.preference.security?" + pane) {
                    NSWorkspace.shared.open(u)
                }
            } else {
                say(earStatus.isEmpty ? "I can't hear anything yet."
                                      : "I can't listen right now — " + earStatus)
            }
            return
        }
        armed = true
        setBubble("Go ahead \u{2014} I'm listening. Click me to stop.")
    }

    /// One finished utterance. Decide whether it was aimed at him at all.
    func heard(_ raw: String) {
        // One word followed by a pause is not somebody addressing you. The
        // end-of-turn gap is 1.1s, so "Also" counts as a complete utterance
        // unless there is a floor on what a turn even is.
        let words = raw.split(whereSeparator: { $0 == " " || $0 == "\n" }).count
        guard armed || words >= 3 else { return }
        guard let question = addressedQuestion(raw, armed: armed) else {
            return          // heard, not for him — and silence is the answer
        }
        busy = true
        setBubble("\u{201C}" + question + "\u{201D}")
        DispatchQueue.global().async {
            let answer = Meditate.advise(question)
            DispatchQueue.main.async {
                self.busy = false
                self.say(answer.isEmpty ? "I don't know that one yet." : answer)
            }
        }
    }

    func writeStatus() {
        var parts: [String] = []
        parts.append("ear=" + (ear.running ? "on" : "off"))
        parts.append("status=" + (earStatus.isEmpty ? "?" : earStatus))
        // raw TCC verdicts: 0 notDetermined, 1 restricted, 2 denied, 3 authorized
        parts.append("mic=" + String(AVCaptureDevice.authorizationStatus(for: .audio).rawValue))
        parts.append("speech=" + String(SFSpeechRecognizer.authorizationStatus().rawValue))
        parts.append(String(format: "level=%.3f", Double(ear.level)))
        parts.append(String(format: "floor=%.3f", Double(ear.noiseFloor)))
        parts.append(String(format: "quiet=%.1f", ear.quietFor))
        parts.append("heard=" + String(ear.heardSoFar.prefix(40)))
        parts.append("speaking=" + (mouth.speaking ? "yes" : "no"))
        parts.append(String(format: "mouth=%.3f", Double(mouth.drive)))
        parts.append("armed=" + (armed ? "yes" : "no"))
        // Which connections are actually live, checked at runtime rather than
        // by reading the source and calling it wired.
        var wired: [String] = []
        if ear.onPartial   != nil { wired.append("ear>bubble") }
        if ear.onUtterance != nil { wired.append("ear>brain") }
        if mouth.onFinish  != nil { wired.append("mouth>unmute") }
        if ghost.onClick   != nil { wired.append("click>listen") }
        if ghost.hearLevel == ear.level { wired.append("mic>face") }
        parts.append("wired=" + wired.joined(separator: ","))
        // Cached from the 20s poll. Asking the server here would block the
        // main thread for up to 8s every 2s — a diagnostic that freezes the
        // thing it is diagnosing is not a diagnostic.
        parts.append("server=" + serverState)
        let vis = [("mic", micBtn), ("ask", askBtn), ("yes", yesBtn)]
            .map { n, b -> String in
                let f = b?.frame ?? .zero
                return "\(n):\(b?.isHidden == false ? "shown" : "hidden")@\(Int(f.minY))-\(Int(f.maxY))"
            }.joined(separator: ",")
        parts.append("btns=" + vis)
        parts.append("root=" + String(Int(window.contentView?.bounds.height ?? 0)))
        parts.append("said=" + String(bubble.stringValue.prefix(70)))
        let line: String = parts.joined(separator: "  ")
        try? line.write(toFile: "/tmp/casper-status.txt", atomically: true,
                        encoding: String.Encoding.utf8)
    }

    /// Listening is OFF until you say otherwise, and it stays however you left
    /// it. He used to open the microphone the moment he launched — the orange
    /// dot appears, he starts transcribing the room, and nobody asked him to.
    /// A companion does not get to decide that for you.
    static let micKey = "micEnabled"

    var micEnabled: Bool {
        get { UserDefaults.standard.bool(forKey: App.micKey) }
        set { UserDefaults.standard.set(newValue, forKey: App.micKey) }
    }

    @objc func toggleMic() {
        if ear.running {
            armed = false
            ear.stop()
            micEnabled = false
            setBubble("Off. I won't hear anything until you switch me back on.")
        } else {
            micEnabled = true
            Ear.requestAccess { [weak self] ok, why in
                guard let self = self else { return }
                self.earStatus = ok ? "listening" : why
                if ok {
                    self.ear.start()
                    if !self.ear.running { self.earStatus = self.ear.lastError }
                }
                self.refreshMicButton()
                self.setBubble(self.ear.running
                    ? "Microphone on. Say my name, or click me and just talk."
                    : "Can't listen — " + self.earStatus)
            }
        }
        refreshMicButton()
    }

    func refreshMicButton() {
        let on = ear.running
        style(micBtn, title: on ? "I'm listening" : "Talk to me", accent: on)
        micBtn.toolTip = on ? "Click to stop listening"
                            : "Click to let Casper hear you"
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
        setBubble("Thinking…")
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
        ("thinking", .thinking, 0.6), ("blink", .idle, 0.25),
        ("happy", .idle, 0.4), ("surprised", .idle, 0.4),
        ("sleepy", .idle, 0.25), ("hop", .idle, 0.4),
        ("arrive1", .idle, 0.4), ("arrive2", .idle, 0.4),
        ("arrive3", .idle, 0.4), ("arrive4", .idle, 0.4)]
    for (name, mood, glow) in moods {
        v.mood = mood; v.glow = glow
        v.feel(.neutral, for: 0)       // a timed feeling would bleed into the next frame
        if name.hasPrefix("arrive") {
            // the entrance, sampled at four points along its 1.5s
            let stops = ["arrive1": 14, "arrive2": 30, "arrive3": 48, "arrive4": 82]
            v.materialize()
            for _ in 0..<(stops[name] ?? 20) { v.tick(dt: 1.0 / 60) }
        } else {
            for _ in 0..<90 { v.tick(dt: 1.0 / 60) }
        }
        switch name {
        case "blink":     v.forceBlink(); v.tick(dt: 1.0 / 60)
        case "happy":     v.celebrate(); for _ in 0..<22 { v.tick(dt: 1.0 / 60) }
        case "surprised": v.startle();   for _ in 0..<8  { v.tick(dt: 1.0 / 60) }
        case "sleepy":    v.feel(.sleepy, for: 5); for _ in 0..<30 { v.tick(dt: 1.0 / 60) }
        case "hop":       v.bounce();    for _ in 0..<11 { v.tick(dt: 1.0 / 60) }
        default: break
        }
        guard let rep = v.bitmapImageRepForCachingDisplay(in: v.bounds) else { continue }
        rep.size = v.bounds.size
        v.cacheDisplay(in: v.bounds, to: rep)
        if let png = rep.representation(using: .png, properties: [:]) {
            try? png.write(to: URL(fileURLWithPath: "\(dir)/casper-\(name).png"))
            print("wrote casper-\(name).png")
        }
    }
}
