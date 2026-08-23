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
import UserNotifications
import Carbon.HIToolbox

// MARK: - talking to meditate (the same commands the terminal runs)

struct Brief {
    var headline: String
    var action: String      // "meditate fix" / "meditate go" / ""
    var kind: String        // repair | goals | task | still | clear
    var canInterrupt: Bool
    var facts: Int
    var verified: Int
    var fleetRunning: Int = 0
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
                        facts: 0, verified: 0,
                        fleetRunning: j["fleet_running"] as? Int ?? 0)
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

    /// The short list of things worth attention — one spoken line each, with
    /// the command that would deal with it.
    static func agenda() -> [(say: String, action: String)] {
        let out = run([skillDir + "/voice.py", "--agenda"], timeout: 40)
        return out.split(separator: "\n").compactMap { line in
            let parts = String(line).components(separatedBy: "\t")
            guard let first = parts.first, !first.isEmpty else { return nil }
            return (say: first, action: parts.count > 1 ? parts[1] : "")
        }
    }

    /// What the slow step running right now is doing. Read straight off the
    /// file rather than by shelling out — this is polled twice a second while
    /// he works, and a python launch per poll would cost more than the work.
    static func thinkingStep() -> String {
        let p = ("~/.claude/meditation/thinking.jsonl" as NSString).expandingTildeInPath
        guard let d = FileManager.default.contents(atPath: p),
              let j = try? JSONSerialization.jsonObject(with: d) as? [String: Any],
              let step = j["step"] as? String, !step.isEmpty,
              let ts = j["ts"] as? Double,
              Date().timeIntervalSince1970 - ts < 120     // stale = a crash
        else { return "" }
        let detail = (j["detail"] as? String) ?? ""
        return detail.isEmpty ? step : step + " \u{2014} " + detail
    }

    /// What the agents you started are doing. Same server, same trail.
    static func fleet() -> [(goal: String, ticked: Bool, mins: Int, window: String)] {
        guard let url = URL(string: "http://127.0.0.1:7711/api/state") else { return [] }
        var req = URLRequest(url: url)
        req.setValue("1", forHTTPHeaderField: "X-Meditate")
        req.timeoutInterval = 6
        var out: [(String, Bool, Int, String)] = []
        let sem = DispatchSemaphore(value: 0)
        URLSession.shared.dataTask(with: req) { data, _, _ in
            defer { sem.signal() }
            guard let d = data,
                  let j = try? JSONSerialization.jsonObject(with: d) as? [String: Any],
                  let rows = j["fleet"] as? [[String: Any]] else { return }
            out = rows.map { r in
                ((r["goal"] as? String) ?? "?",
                 (r["milestone_ticked"] as? Bool) ?? false,
                 (r["dispatched_min"] as? Int) ?? 0,
                 (r["window_id"] as? String) ?? "")
            }
        }.resume()
        _ = sem.wait(timeout: .now() + 8)
        return out
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
        if action.hasPrefix("clear") {
            let goal = action.dropFirst("clear".count)
                .trimmingCharacters(in: .whitespaces)
            return postAct("clear", goal) ?? "Couldn't reach the console."
        }
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
    static func postAct(_ verb: String, _ arg: String = "") -> String? {
        guard let url = URL(string: "http://127.0.0.1:7711/api/act") else { return nil }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.timeoutInterval = 30
        req.setValue("1", forHTTPHeaderField: "X-Meditate")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(
            withJSONObject: ["action": verb, "arg": arg])
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
/// How far apart two words are, letter by letter.
func editDistance(_ a: String, _ b: String) -> Int {
    let x = Array(a), y = Array(b)
    if x.isEmpty || y.isEmpty { return max(x.count, y.count) }
    var prev = Array(0...y.count)
    for i in 1...x.count {
        var cur = [i]
        for j in 1...y.count {
            cur.append(min(prev[j] + 1, cur[j - 1] + 1,
                           prev[j - 1] + (x[i - 1] == y[j - 1] ? 0 : 1)))
        }
        prev = cur
    }
    return prev[y.count]
}

/// Was the first word his name, allowing for it being misheard?
///
/// Measured by talking at him: "Casper" came back as "Rahul", and on other
/// passes "Caspar" and "Jasper". Demanding the exact spelling of a name the
/// recogniser was never taught means he answers only when it guesses right —
/// which from the outside is indistinguishable from not listening at all.
func soundsLikeHisName(_ token: String) -> Bool {
    let t = token.lowercased().trimmingCharacters(
        in: CharacterSet.alphanumerics.inverted)
    guard t.count >= 4, t.count <= 9 else { return false }
    if t.contains("asper") || t.contains("aspar") || t.contains("caspe") {
        return true
    }
    // ONE edit, not two. At two, "faster" is his name — measured: "faster
    // please" woke him up. The substring rules above already cover the
    // mishearings that actually happen, and they are specific.
    return editDistance(t, "casper") <= 1
}

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
    if !addressed {
        let words = text.split(separator: " ").map(String.init)
        if let first = words.first, soundsLikeHisName(first) {
            addressed = true
            question = words.dropFirst().joined(separator: " ")
                .trimmingCharacters(in: CharacterSet(charactersIn: " ,.?!"))
        } else if lower.contains("casper") {
            addressed = true
        }
    }
    guard addressed, question.count > 2 else { return nil }
    return question
}


/// What a heard sentence IS, decided by code before any model sees it.
/// The no-ship refusal and the command lane are behaviour guarantees, so they
/// live here — callable from the app and from `--hear`, because a guarantee
/// that can only be exercised by talking at a live window is a guarantee
/// nobody checks.
enum RouteKind {
    case refuse(String)
    case offer(verb: String, line: String)
    case advise
}

func routeDecision(_ question: String) -> RouteKind {
    let lower = question.lowercased()
    for word in ["push", "deploy", "ship it", "release", "merge"]
    where lower.contains(word) {
        return .refuse("I won't push or deploy by voice \u{2014} that one "
                       + "stays in the terminal with you.")
    }
    let commands: [(String, [String], String)] = [
        ("fix", ["fix", "repair"],
         "I can go repair what stopped being true. Yes?"),
        ("grade", ["grade", "check your facts"],
         "I can re-check everything I know. Yes?"),
        ("go", ["run the fleet", "dispatch", "launch", "start the work",
                "get someone on"],
         "I can put agents on the open work. Yes?")]
    for (verb, phrases, line) in commands
    where phrases.contains(where: { lower.contains($0) }) {
        return .offer(verb: verb, line: line)
    }
    return .advise
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

    // ---- micro-expressions -------------------------------------------------
    // Brief, involuntary-looking twitches. Nobody consciously notices them;
    // without them a face reads as a puppet holding still between commands.
    private enum Micro { case none, browRaise, smileFlick, squint, sniff }
    private var micro: Micro = .none
    private var microUntil: CGFloat = 0
    private var nextMicroAt: CGFloat = 1.8

    /// How interested he is in you right now — rises as your cursor comes
    /// near. Drives pupil size, because pupils widening is the one cue people
    /// read as warmth without ever being able to name it.
    private var interest: CGFloat = 0

    /// Nothing symmetrical looks alive. One eye is a hair bigger and blinks a
    /// beat sooner than the other, forever.
    private let asym: CGFloat = 0.055

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

    /// Where your cursor is, as a gaze direction — nil when it is too far
    /// away to care about.
    ///
    /// This is the cheapest large thing in the whole mascot: a character whose
    /// eyes follow you has been noticed by you, and one whose eyes wander at
    /// random is a screensaver. Everything else here is decoration on top.
    private func cursorGaze() -> (CGPoint, CGFloat)? {
        guard let win = window else { return nil }
        let p = convert(win.convertPoint(fromScreen: NSEvent.mouseLocation), from: nil)
        let cx = bounds.width / 2, cy = bounds.height * 0.42
        let dx = p.x - cx, dy = p.y - cy
        let dist = hypot(dx, dy)
        guard dist < 520 else { return nil }
        let near = max(0, 1 - dist / 380)           // 1 when right on him
        return (CGPoint(x: max(-1, min(1, dx / 150)),
                        y: max(-1, min(1, dy / 150))), near)
    }

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
                                life: 0.62))   // gone at the moment he lands
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
            case .hop:
                // crouch first, THEN spring. Anticipation is the difference
                // between a jump and a teleport upward.
                hopV += 95
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.13) { [weak self] in
                    self?.hopV -= 260
                }
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

        // ---- micro-expressions ------------------------------------------------
        if t > microUntil { micro = .none }
        if micro == .none && t > nextMicroAt && mood != .speaking {
            let bag: [Micro] = interest > 0.35
                ? [.browRaise, .smileFlick, .browRaise, .sniff]
                : [.squint, .sniff, .browRaise]
            micro = bag.randomElement() ?? .browRaise
            microUntil = t + CGFloat.random(in: 0.16...0.30)
            nextMicroAt = t + CGFloat.random(in: 1.6...4.2)
        }

        // ---- gaze -----------------------------------------------------------
        let cursor = cursorGaze()
        interest += ((cursor?.1 ?? 0) - interest) * min(1, dt * 4)
        if mood == .thinking {
            gazeTo = CGPoint(x: -0.5, y: -0.6)
        } else if mood == .listening {
            gazeTo = CGPoint(x: 0, y: -0.25)          // look AT you while you talk
        } else if let c = cursor, act == .none, c.1 > 0.08 {
            gazeTo = c.0                              // he is watching your cursor
            gazeAt = t + 0.4
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
        if micro == .smileFlick { mouthTarget += 0.14 }
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
    var onDoubleClick: (() -> Void)?
    var onRightClick: ((NSEvent) -> Void)?
    private var downAt = NSPoint.zero
    private var pendingClick: DispatchWorkItem?

    override func rightMouseDown(with e: NSEvent) { onRightClick?(e) }

    override func mouseDown(with e: NSEvent) { downAt = e.locationInWindow }
    override func mouseUp(with e: NSEvent) {
        let d = hypot(e.locationInWindow.x - downAt.x, e.locationInWindow.y - downAt.y)
        guard d < 4 else { super.mouseUp(with: e); return }
        // A double click must not ALSO fire the single-click action, or opening
        // the dashboard would silently toggle the microphone on the way. The
        // single click waits a quarter second to find out which it was.
        if e.clickCount >= 2 {
            pendingClick?.cancel()
            pendingClick = nil
            bounce()
            onDoubleClick?()
            return
        }
        let w = DispatchWorkItem { [weak self] in
            self?.bounce()
            self?.onClick?()
        }
        pendingClick = w
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.25, execute: w)
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
        // While arriving, scale about his CENTRE, not his top edge. Scaling
        // from the top anchor left a tiny ghost stranded above the point the
        // sparks were converging on — the two halves of the effect
        // disagreeing about where he was about to be.
        let arriveShift: CGFloat = appear < 1
            ? (h * 0.10 + h * 0.70 / 2) - (h * 0.10 + bh / 2) : 0
        let ty = h * 0.10 + bob + arriveShift
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
        let eyeY = ty + bh * 0.435      // low on the face reads younger
        let eyeDX = bw * 0.225
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
        // pupils widen as you come near. Nobody can name this cue; everybody
        // reads it as warmth.
        let dilate = 1 + interest * 0.12
        // micro-expressions: a brow raise or a squint, 0.2s, involuntary
        let browed: CGFloat = micro == .browRaise ? 1.22
                            : micro == .squint    ? 0.62 : 1.0
        let eyeW = bw * 0.135 * wide * dilate
        let eyeH = eyeW * 1.22 * openAmt * lid * browed

        for (i, sx) in [-eyeDX, eyeDX].enumerated() {
            // nothing symmetrical looks alive: one eye a hair bigger, and it
            // shuts a beat before the other
            let k = 1 + (i == 0 ? asym : -asym)
            let eyeW = eyeW * k, eyeH = eyeH * k
            let jitter: CGFloat = micro == .sniff ? sin(t * 34) * 0.5 : 0
            let ex = cx + sx + gaze.x * bw * 0.062 + jitter
            let ey = eyeY + gaze.y * bh * 0.048

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
            if blink > 0.55 {
                // two highlights, not one. A single dot is a doll's eye; a big
                // one with a small one behind it is wet, and wet reads alive.
                NSColor(white: 1, alpha: 0.94 * blink).setFill()
                NSBezierPath(ovalIn: NSRect(x: ex - eyeW * 0.10, y: ey - eyeH * 0.28,
                                            width: eyeW * 0.34, height: eyeW * 0.34)).fill()
                NSColor(white: 1, alpha: 0.45 * blink).setFill()
                NSBezierPath(ovalIn: NSRect(x: ex + eyeW * 0.14, y: ey + eyeH * 0.16,
                                            width: eyeW * 0.11, height: eyeW * 0.11)).fill()
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


/// The speech card: rounded, with a tail pointing up at him, and a real
/// shadow. A flat grey slab with square text in it is a debug console; the
/// tail is what makes the words HIS rather than the app's.
final class BubbleView: NSView {
    var tailX: CGFloat = 0.5          // 0..1 across the width
    override var isFlipped: Bool { true }

    override func draw(_ dirty: NSRect) {
        guard let ctx = NSGraphicsContext.current?.cgContext else { return }
        let r = bounds.insetBy(dx: 1, dy: 1)
        let tailH: CGFloat = 7, radius: CGFloat = 12
        let body = NSRect(x: r.minX, y: r.minY + tailH,
                          width: r.width, height: r.height - tailH)
        let p = NSBezierPath(roundedRect: body, xRadius: radius, yRadius: radius)
        // the tail, centred under him
        let tx = r.minX + r.width * tailX
        let t = NSBezierPath()
        t.move(to: NSPoint(x: tx - 7, y: body.minY + 0.5))
        t.line(to: NSPoint(x: tx, y: r.minY))
        t.line(to: NSPoint(x: tx + 7, y: body.minY + 0.5))
        t.close()
        p.append(t)

        ctx.saveGState()
        ctx.setShadow(offset: CGSize(width: 0, height: 2), blur: 10,
                      color: NSColor(white: 0, alpha: 0.38).cgColor)
        NSColor(srgbRed: 0.10, green: 0.10, blue: 0.115, alpha: 0.95).setFill()
        p.fill()
        ctx.restoreGState()

        // a hairline top edge: it lifts the card off the wallpaper behind it
        NSColor(white: 1, alpha: 0.07).setStroke()
        p.lineWidth = 1
        p.stroke()
    }
}


// MARK: - telling you a job finished

/// A completion notice you can click.
///
/// The notice used to be posted by `osascript` from whichever python process
/// happened to be running, so clicking it activated Terminal and dropped you
/// in a transcript — the raw log of the thing, not the place you go to decide
/// what is next. Posted from THIS app it can carry an action, and the action
/// opens the dashboard.
final class Notifier: NSObject, UNUserNotificationCenterDelegate {
    static let shared = Notifier()
    static let dashboard = "http://127.0.0.1:7711"
    private var ready = false

    func start() {
        let c = UNUserNotificationCenter.current()
        c.delegate = self
        c.requestAuthorization(options: [.alert, .sound]) { ok, _ in
            DispatchQueue.main.async { self.ready = ok }
        }
        c.setNotificationCategories([
            UNNotificationCategory(identifier: "done",
                                   actions: [UNNotificationAction(
                                        identifier: "open",
                                        title: "Open dashboard",
                                        options: [.foreground])],
                                   intentIdentifiers: [])
        ])
    }

    func post(title: String, body: String) {
        guard ready else { return }
        let c = UNMutableNotificationContent()
        c.title = title
        c.body = body
        c.sound = .default
        c.categoryIdentifier = "done"
        UNUserNotificationCenter.current().add(
            UNNotificationRequest(identifier: UUID().uuidString,
                                  content: c, trigger: nil))
    }

    /// Clicking the notice, or its button, opens the dashboard.
    func userNotificationCenter(_ c: UNUserNotificationCenter,
                                didReceive r: UNNotificationResponse,
                                withCompletionHandler done: @escaping () -> Void) {
        if let u = URL(string: Notifier.dashboard) { NSWorkspace.shared.open(u) }
        done()
    }

    /// Show it even when Casper is the app in front.
    func userNotificationCenter(_ c: UNUserNotificationCenter,
                                willPresent n: UNNotification,
                                withCompletionHandler done:
                                    @escaping (UNNotificationPresentationOptions) -> Void) {
        done([.banner, .sound])
    }
}


// MARK: - a key that reaches him from anywhere

/// One key combination, live in every app, that turns Casper on and points him
/// at you.
///
/// The fn key was the ask, and fn is the one key this cannot be: it is a raw
/// modifier, so catching a double-tap means a system-wide keyboard monitor,
/// which means Accessibility permission and Casper reading every keystroke you
/// make. For a tool whose whole promise is that your work stays on your
/// machine, that is the wrong trade for a shortcut. A registered hot key
/// reaches him just as fast and the system only ever hands over this one combo.
///
/// The combos are tried in order and the FIRST FREE one wins — registration
/// fails outright when something else already owns it, so a silent clash with
/// Spotlight or a launcher is not possible.
final class Hotkey {
    static let shared = Hotkey()
    var onFire: (() -> Void)?
    private(set) var describe = "none"
    /// How many times the system has actually handed us the key. Without this
    /// there is no way to tell "the handler never ran" from "it ran and the
    /// action did nothing", and those need opposite fixes.
    fileprivate(set) var fires = 0
    private var ref: EventHotKeyRef?

    private static let candidates: [(String, UInt32, UInt32)] = [
        ("\u{2325}Space", UInt32(kVK_Space), UInt32(optionKey)),
        ("\u{2303}\u{2325}Space", UInt32(kVK_Space), UInt32(optionKey | controlKey)),
        ("\u{2303}\u{2325}C", UInt32(kVK_ANSI_C), UInt32(optionKey | controlKey)),
    ]

    func install() {
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard),
                                 eventKind: UInt32(kEventHotKeyPressed))
        InstallEventHandler(GetApplicationEventTarget(), { _, _, _ -> OSStatus in
            DispatchQueue.main.async {
                Hotkey.shared.fires += 1
                Hotkey.shared.onFire?()
            }
            return noErr
        }, 1, &spec, nil, nil)

        for (label, key, mods) in Hotkey.candidates {
            var id = EventHotKeyID(signature: OSType(0x43535052), id: 1)  // 'CSPR'
            var r: EventHotKeyRef?
            if RegisterEventHotKey(key, mods, id, GetApplicationEventTarget(),
                                   0, &r) == noErr, r != nil {
                ref = r
                describe = label
                return
            }
            _ = id
        }
    }
}


/// The fleet, on his face.
///
/// The owner's words: "the fleet is very important that user knows what he has
/// launched — apart from the dashboard, even in the mascot we should see those
/// sessions visually." Knowing something is running and having to open a
/// browser to find out WHAT is half a feature.
///
/// One row per agent: a dot for its state, the goal, how long it has been out.
/// Clicking a row brings that agent's Terminal window to the front, because
/// the next thing you want after "what did I launch" is "show me".
final class FleetView: NSView {
    struct Row { let goal: String; let ticked: Bool; let mins: Int; let window: String }
    var rows: [Row] = [] { didSet { needsDisplay = true } }
    var onPick: ((Row) -> Void)?
    static let rowH: CGFloat = 17

    override var isFlipped: Bool { true }
    static func height(for n: Int) -> CGFloat { n == 0 ? 0 : CGFloat(n) * rowH + 6 }

    override func mouseUp(with e: NSEvent) {
        let p = convert(e.locationInWindow, from: nil)
        let i = Int((p.y - 3) / FleetView.rowH)
        guard i >= 0, i < rows.count else { return }
        onPick?(rows[i])
    }

    override func draw(_ dirty: NSRect) {
        guard !rows.isEmpty else { return }
        let w = bounds.width
        // Its own card. Without it the row text sat directly on the wallpaper,
        // and light-on-light is unreadable on half the desktops in the world.
        let card = NSBezierPath(roundedRect: bounds.insetBy(dx: 0, dy: 1),
                                xRadius: 10, yRadius: 10)
        NSColor(srgbRed: 0.10, green: 0.10, blue: 0.115, alpha: 0.92).setFill()
        card.fill()
        NSColor(white: 1, alpha: 0.06).setStroke()
        card.lineWidth = 1
        card.stroke()
        for (i, r) in rows.enumerated() {
            let y = 3 + CGFloat(i) * FleetView.rowH

            // the dot IS the state, so the row reads before it is read
            let colour: NSColor = r.ticked
                ? NSColor(srgbRed: 0.29, green: 0.70, blue: 0.38, alpha: 1)   // done
                : r.window.isEmpty
                    ? NSColor(srgbRed: 0.55, green: 0.55, blue: 0.58, alpha: 1) // untracked
                    : NSColor(srgbRed: 0.93, green: 0.72, blue: 0.24, alpha: 1) // working
            colour.setFill()
            NSBezierPath(ovalIn: NSRect(x: 2, y: y + 5, width: 6, height: 6)).fill()

            let mins = r.mins >= 60
                ? String(format: "%dh", r.mins / 60) : "\(r.mins)m"
            let right = NSAttributedString(string: mins, attributes: [
                .font: App.rounded(9.5, .medium),
                .foregroundColor: NSColor(calibratedWhite: 0.62, alpha: 1)])
            let rw = right.size().width
            right.draw(at: NSPoint(x: w - rw - 2, y: y + 2))

            let name = NSMutableAttributedString(string: r.goal, attributes: [
                .font: App.rounded(10.5, .medium),
                .foregroundColor: NSColor(calibratedWhite: r.ticked ? 0.58 : 0.90,
                                          alpha: 1)])
            if r.ticked {          // struck through: it is finished, not pending
                name.addAttribute(.strikethroughStyle,
                                  value: NSUnderlineStyle.single.rawValue,
                                  range: NSRange(location: 0, length: name.length))
            }
            let avail = w - rw - 18
            var shown = name
            if name.size().width > avail {
                let cut = max(4, Int(Double(r.goal.count) * Double(avail / name.size().width)) - 1)
                shown = NSMutableAttributedString(
                    string: String(r.goal.prefix(cut)) + "\u{2026}",
                    attributes: name.attributes(at: 0, effectiveRange: nil))
            }
            shown.draw(at: NSPoint(x: 13, y: y + 1))
        }
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
    var fleetBtn: NSButton!
    var stopBtn: NSButton!
    var muteBtn: NSButton!

    /// Quiet mode: he never speaks or offers UNPROMPTED. Click him or say his
    /// name and he still answers — this disables the intrusion, not the tool.
    static let quietKey = "casper.quiet"
    /// Muted is not the same as quiet, and folding them together would lose
    /// one of them. `quiet` = do not VOLUNTEER things. `voiceOff` = never
    /// make a sound; the words still arrive, in writing.
    static let muteKey = "casper.muted"
    var quiet: Bool {
        get { UserDefaults.standard.bool(forKey: App.quietKey) }
        set { UserDefaults.standard.set(newValue, forKey: App.quietKey) }
    }

    var voiceOff: Bool {
        get { UserDefaults.standard.bool(forKey: App.muteKey) }
        set {
            UserDefaults.standard.set(newValue, forKey: App.muteKey)
            // Flush now: this app gets killed rather than quit, and an unflushed
            // preference is one that silently forgets what you chose.
            UserDefaults.standard.synchronize()
        }
    }
    /// The full spoken introduction happens ONCE, ever. Repeating the whole
    /// speech at every launch was most of what made him feel intrusive.
    static let introducedKey = "casper.introduced"
    var fleetRunning = 0
    var lastStep = ""
    var lastBubbleAt = Date()
    var shotMode = false        // rendering the UI for review: no mic, no polling
    var micBtn: NSButton!
    var closeBtn: NSButton!
    var statusItem: NSStatusItem!
    var fleetView: FleetView!
    var fleetH: CGFloat = 0
    let mouth = Mouth()
    let ear = Ear()
    var armed = false          // click him = one turn without his name
    var earStatus = ""
    var serverState = "?"

    var pendingAction = ""
    /// What "Yes" means at this moment: read the list, or run the command.
    var pendingKind = ""
    var agendaItems: [(say: String, action: String)] = []
    var lastSpoken = ""
    var busy = false
    var muteGraceFrames = 0

    func applicationDidFinishLaunching(_ n: Notification) {
        let W: CGFloat = 268, H: CGFloat = 248
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

        fleetView = FleetView(frame: NSRect(x: 10, y: 110, width: W - 20, height: 0))
        fleetView.onPick = { [weak self] r in self?.showAgent(r) }
        root.addSubview(fleetView)

        ghost = GhostView(frame: NSRect(x: 68, y: 100, width: 132, height: 132))
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
        // The card he speaks from: tail pointing up at him, real shadow, and
        // its own drawing rather than a tinted rectangle.
        let card = BubbleView(frame: NSRect(x: 8, y: 38, width: W - 16, height: 68))
        card.wantsLayer = true
        bubbleBox = card
        root.addSubview(bubbleBox)

        bubble = NSTextField(wrappingLabelWithString: "")
        bubble.frame = NSRect(x: 12, y: 14, width: W - 40, height: 48)
        // Left, not centred. Centred text ragged on BOTH sides is harder to
        // read every line after the first, and three lines of it looked like a
        // fortune cookie rather than someone talking to you.
        bubble.alignment = .left
        bubble.font = App.rounded(11.5, .regular)
        bubble.textColor = NSColor(calibratedWhite: 0.95, alpha: 1)
        bubble.drawsBackground = false
        bubble.isBezeled = false
        bubble.isEditable = false
        bubble.isSelectable = false
        bubble.maximumNumberOfLines = 4
        bubble.lineBreakMode = .byWordWrapping     // truncating hid the message
        bubble.cell?.wraps = true
        bubble.cell?.isScrollable = false
        bubbleBox.addSubview(bubble)

        let bf = App.rounded(11.5, .semibold)
        // Two rows share the same strip; each is laid out to fill the width
        // with equal gaps, so nothing is ever clipped and nothing floats.
        let offerRow = ["Yes", "Not now"], ctrlRow = ["Talk", "Ask"]
        _ = offerRow; _ = ctrlRow
        // The fleet switch never moves. It is the one control present in BOTH
        // rows, so laying each row out independently put it underneath "Not
        // now" — two layouts disagreeing about the same button. Pin it right,
        // then fill the space to its left.
        let fleetW = App.widthFor("Stop 88", font: bf)
        let fleetXPos = W - 8 - fleetW
        let leftWidth = fleetXPos - 12   // a real gap before the fleet switch
        let offerX = App.rowX(["Yes", "Not now"], font: bf, width: leftWidth)
        let ctrlX  = App.rowX(["Hearing", "Muted", "Ask"], font: bf, width: leftWidth)

        yesBtn = button("Yes", x: offerX[0].0, w: offerX[0].1)
        yesBtn.target = self; yesBtn.action = #selector(sayYes)
        yesBtn.keyEquivalent = "\r"                 // the obvious answer is the default
        noBtn = button("Not now", x: offerX[1].0, w: offerX[1].1)
        noBtn.target = self; noBtn.action = #selector(sayNo)
        askBtn = button("Ask", x: ctrlX[2].0, w: ctrlX[2].1)
        askBtn.target = self; askBtn.action = #selector(askSomething)
        fleetBtn = button("Start", x: fleetXPos, w: fleetW)
        fleetBtn.target = self; fleetBtn.action = #selector(toggleFleet)
        tinted(fleetBtn, title: "Start", tint: .green)
        root.addSubview(fleetBtn)

        muteBtn = button("Mute", x: ctrlX[1].0, w: ctrlX[1].1)
        muteBtn.target = self; muteBtn.action = #selector(toggleMute)
        root.addSubview(muteBtn)

        micBtn = button("Talk", x: ctrlX[0].0, w: ctrlX[0].1)
        micBtn.target = self; micBtn.action = #selector(toggleMic)
        // Stop: visible only while he is speaking, in the offer slot — the
        // moment he talks, the most important control is the one that ends it.
        stopBtn = button("Stop", x: offerX[0].0, w: offerX[0].1)
        stopBtn.target = self; stopBtn.action = #selector(stopTalking)
        tinted(stopBtn, title: "Stop", tint: .red)
        stopBtn.isHidden = true
        root.addSubview(stopBtn)

        ghost.onRightClick = { [weak self] e in self?.showMenu(e) }
        ghost.onDoubleClick = { [weak self] in self?.openDashboard() }
        ghost.toolTip = "Click to talk · double-click for the dashboard · "
                      + "right-click for more"
        // Somewhere to click when you want him gone. A companion with no way
        // to dismiss it is not a companion.
        closeBtn = NSButton(title: "\u{00D7}", target: self,
                            action: #selector(closeCasper))
        closeBtn.frame = NSRect(x: W - 24, y: H - 24, width: 18, height: 18)
        closeBtn.isBordered = false
        closeBtn.wantsLayer = true
        closeBtn.layer?.cornerRadius = 9
        closeBtn.layer?.backgroundColor =
            NSColor(calibratedWhite: 0.13, alpha: 0.75).cgColor
        closeBtn.attributedTitle = NSAttributedString(string: "\u{00D7}", attributes: [
            .foregroundColor: NSColor(calibratedWhite: 0.85, alpha: 1),
            .font: NSFont.systemFont(ofSize: 12, weight: .medium)])
        closeBtn.toolTip = "Hide Casper — the ghost in the menu bar brings him back"
        [yesBtn, noBtn, askBtn, micBtn, closeBtn].forEach { root.addSubview($0!) }
        refreshMuteButton()
        showOffer(false)
        refreshMicButton()
        setBubble("")

        // The very first run: he gathers himself out of nothing in front of
        // you, and greet() lands as he finishes forming. Once only — a trick
        // you have seen twice is furniture, and every launch after this he is
        // simply already there.
        let firstEver = !UserDefaults.standard.bool(forKey: App.arrivedKey)
        if firstEver {
            UserDefaults.standard.set(true, forKey: App.arrivedKey)
            // Flush NOW. UserDefaults writes lazily, and an accessory app that
            // gets killed rather than quit never reaches the flush — so he
            // made his entrance again on every single launch.
            UserDefaults.standard.synchronize()
            ghost.materialize()
        }

        Hotkey.shared.onFire = { [weak self] in self?.summoned() }
        Hotkey.shared.install()
        Notifier.shared.start()
        window.makeKeyAndOrderFront(nil)
        // Policy FIRST, then the status item. Changing the activation policy
        // tears down the app's connection to the status bar, so an item made
        // before this line is created and then silently discarded — the
        // menu-bar ghost simply never appeared.
        NSApp.setActivationPolicy(.accessory)       // no Dock icon; lives in the menu bar
        installMenuBar()

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
            self.stopBtn.isHidden = !self.mouth.speaking
            // Mute watchdog. say() mutes the ear and trusts onFinish to
            // unmute — but a render that produces NO audio never fires
            // onFinish, and that left him deaf forever with muted=yes and no
            // way home. Muted with nothing playing for 6s straight = the
            // promise was broken; unmute. (6s > the warm render gap; and if
            // speech does start later, onStart re-mutes.)
            if self.ear.muted && !self.mouth.speaking {
                self.muteGraceFrames += 1
                if self.muteGraceFrames > 360 {
                    self.ear.muted = false
                    self.ear.freshTurn()
                    self.muteGraceFrames = 0
                }
            } else {
                self.muteGraceFrames = 0
            }
            self.ghost.tick(dt: 1.0 / 60)
        }
        // ---- the ear -------------------------------------------------------
        // He listens all the time but only ANSWERS when addressed. Anything
        // else and a companion turns into a thing that talks over your calls.
        mouth.onFinish = { [weak self] in
            guard let self = self else { return }
            self.ear.muted = false            // safe to hear you again
            self.ear.freshTurn()              // drop fragments heard pre-mute
        }
        // The mute must track ACTUAL speech, not the promise of it: say()
        // mutes early (so the render gap is covered), the watchdog below
        // unmutes if that promise is never kept, and this re-mutes the moment
        // sound really starts.
        let prevOnStart = mouth.onStart
        mouth.onStart = { [weak self] in
            prevOnStart?()
            self?.ear.muted = true
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
            if shotMode { return }
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

        Timer.scheduledTimer(withTimeInterval: 20, repeats: true) { _ in
            self.check()
            self.watchFleet()
        }
        // the switch tracks reality on its own clock: a 20s lag on "is work
        // running" is long enough to press start on something already running
        // While he is working, say WHAT he is working on. A companion that
        // goes quiet for forty seconds has, from your side of the screen,
        // hung — and a spinner only tells you something you had assumed.
        Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { _ in
            guard self.busy, !self.mouth.speaking else { return }
            let step = Meditate.thinkingStep()
            guard !step.isEmpty, step != self.lastStep else { return }
            self.lastStep = step
            self.setBubble(step + "\u{2026}")
        }
        Timer.scheduledTimer(withTimeInterval: 6, repeats: true) { _ in self.idleHint() }
        Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { _ in
            DispatchQueue.global().async {
                guard let b = Meditate.briefFromServer() else { return }
                DispatchQueue.main.async { self.refreshFleetButton(b.fleetRunning) }
            }
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + (firstEver ? 2.1 : 1.2)) {
            self.greet()
        }
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
        b.layer?.cornerRadius = 9
        // Controls need to sit ON something. Flat pills on a transparent
        // window read as stickers; one soft shadow grounds the whole strip.
        b.layer?.shadowColor = NSColor.black.cgColor
        b.layer?.shadowOpacity = 0.32
        b.layer?.shadowRadius = 3
        b.layer?.shadowOffset = CGSize(width: 0, height: -1)
        b.layer?.backgroundColor = NSColor(calibratedWhite: 0.13, alpha: 0.92).cgColor
        style(b, title: title, accent: false)
        return b
    }

    /// Colour carries the verb. Green starts work, red stops it, amber is the
    /// affirmative answer to a question he asked. Nobody should have to read a
    /// 60px label to find out which button is the dangerous one.
    enum Tint { case neutral, amber, green, red }

    func tinted(_ b: NSButton, title: String, tint: Tint) {
        let bg: NSColor
        var fg = NSColor(calibratedWhite: 0.97, alpha: 1)
        switch tint {
        case .green:   bg = NSColor(srgbRed: 0.29, green: 0.64, blue: 0.36, alpha: 1)
        case .red:     bg = NSColor(srgbRed: 0.80, green: 0.29, blue: 0.26, alpha: 1)
        case .amber:   bg = NSColor(srgbRed: 0.91, green: 0.71, blue: 0.25, alpha: 1)
                       fg = NSColor(calibratedWhite: 0.10, alpha: 1)
        case .neutral: bg = NSColor(calibratedWhite: 0.13, alpha: 0.92)
        }
        b.attributedTitle = NSAttributedString(string: title, attributes: [
            .foregroundColor: fg,
            .font: App.rounded(11.5, .semibold),
        ])
        b.layer?.backgroundColor = bg.cgColor
    }

    /// SF Rounded. The stock system font is a control font — correct, cold,
    /// and at odds with a face that has blush on it. Rounded costs nothing and
    /// makes the whole panel read as the same object as the mascot.
    static func rounded(_ size: CGFloat, _ weight: NSFont.Weight) -> NSFont {
        let base = NSFont.systemFont(ofSize: size, weight: weight)
        if let d = base.fontDescriptor.withDesign(.rounded) {
            return NSFont(descriptor: d, size: size) ?? base
        }
        return base
    }

    /// Lay a row of labels across `width` with equal gaps, each pill sized to
    /// its own text. Returns (x, width) per item.
    static func rowX(_ titles: [String], font: NSFont, width: CGFloat,
                     margin: CGFloat = 8) -> [(CGFloat, CGFloat)] {
        var ws = titles.map { widthFor($0, font: font) }
        let gaps = max(1, titles.count - 1)
        let usable = width - margin * 2
        // If the pills do not fit, SHRINK them. The old version floored the
        // gap at 4 and kept laying out past the edge, so the last pill ran
        // underneath the control pinned to its right — measured: "Ask" and
        // "Start" overlapped by 5pt and rendered as one 105pt slab. A layout
        // that silently overflows is worse than one that looks cramped.
        let minGap: CGFloat = 8
        var total = ws.reduce(0, +)
        let need = total + minGap * CGFloat(gaps)
        if need > usable {
            let scale = (usable - minGap * CGFloat(gaps)) / total
            ws = ws.map { max(34, $0 * scale) }
            total = ws.reduce(0, +)
        }
        let gap = max(minGap, (usable - total) / CGFloat(gaps))
        var out: [(CGFloat, CGFloat)] = []
        var x = margin
        for w in ws { out.append((x, w)); x += w + gap }
        return out
    }

    /// Wide enough for its own label, always. "Talk to me" in a 58pt button
    /// was clipped on both sides — a button whose text does not fit is the
    /// loudest possible signal that nobody looked at it.
    static func widthFor(_ title: String, font: NSFont, min: CGFloat = 46) -> CGFloat {
        let w = (title as NSString).size(withAttributes: [.font: font]).width
        return Swift.max(min, ceil(w) + 20)      // 10pt of air each side
    }

    func style(_ b: NSButton, title: String, accent: Bool) {
        let fg = accent ? NSColor(calibratedWhite: 0.10, alpha: 1)
                        : NSColor(calibratedWhite: 0.93, alpha: 1)
        b.attributedTitle = NSAttributedString(string: title, attributes: [
            .foregroundColor: fg,
            .font: App.rounded(11.5, .semibold),
        ])
        b.layer?.backgroundColor = accent
            ? NSColor(srgbRed: 0.91, green: 0.71, blue: 0.25, alpha: 1).cgColor
            : NSColor(calibratedWhite: 0.13, alpha: 0.92).cgColor
    }

    /// An empty bubble is a grey slab taking up half the window. Hide it, and
    /// when there is nothing to say he is just a small ghost on your desktop.
    func setBubble(_ text: String) {
        lastBubbleAt = Date()
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
        muteBtn.isHidden = on
        // ...except the fleet switch, which stays. Whether work is running is
        // not a question he asked you, and you should always be able to stop it.
        fleetBtn.isHidden = false
    }

    /// The delivery ledger. Two one-line files that EVERY mouth writes and
    /// every gate reads:
    ///   last-spoke        mtime = when he last said anything (patience decay,
    ///                     the greeting gap)
    ///   .casper-last.txt  the last delivered line (dedup across mascot,
    ///                     heartbeat notification, page and CLI)
    /// Before this, the mascot spoke through its own Mouth and never stamped
    /// either — so the patience gate read a clock his mouth never set, decayed
    /// to "held for hours", and licensed him to interrupt at any 5s pause.
    static let medDir = ("~/.claude/meditation" as NSString).expandingTildeInPath

    func markDelivered(_ line: String) {
        try? String(Int(Date().timeIntervalSince1970))
            .write(toFile: App.medDir + "/last-spoke", atomically: true,
                   encoding: .utf8)
        try? line.trimmingCharacters(in: .whitespacesAndNewlines)
            .write(toFile: App.medDir + "/.casper-last.txt", atomically: true,
                   encoding: .utf8)
    }

    func alreadyDelivered(_ line: String) -> Bool {
        (try? String(contentsOfFile: App.medDir + "/.casper-last.txt",
                     encoding: .utf8))?
            .trimmingCharacters(in: .whitespacesAndNewlines)
            == line.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    func say(_ text: String) {
        setBubble(text)
        markDelivered(text)
        if voiceOff {
            // Muted means no SOUND, not no answer. The sentence is already in
            // the bubble above; speaking it is the only part being skipped.
            ghost.mood = .idle
            return
        }
        ear.muted = true                 // never answer his own last sentence
        mouth.say(text)
    }

    /// The first thing he ever says. Spoken whatever the timing layer thinks,
    /// because a companion that has never introduced itself is a widget, and
    /// because the rule that waits for a pause had him silent all day — anyone
    /// working at a keyboard is never idle long enough for it to fire.
    func greet() {
        agendaItems = Meditate.agenda()
        let real = agendaItems.filter { !$0.action.isEmpty }

        // Quiet mode: presence without a sound. The glow and the bubble carry
        // everything; nothing is spoken and nothing is offered.
        if quiet {
            ghost.glow = real.isEmpty ? 0.25 : 1.0
            setBubble(real.isEmpty ? ""
                      : "\(real.count) thing\(real.count == 1 ? "" : "s") "
                        + "when you want \(real.count == 1 ? "it" : "them") "
                        + "\u{2014} just ask.")
            showOffer(false)
            return
        }

        let introduced = UserDefaults.standard.bool(forKey: App.introducedKey)
        if !introduced {
            // The full introduction, ONCE. Repeating this monologue at every
            // launch was most of what made him feel intrusive.
            var line = "Hi, I'm Casper. I keep track of what you're building "
                     + "\u{2014} what you told me, what's still true, and what's "
                     + "waiting on you. Ask me anything about your work, or tell "
                     + "me to fix something and I'll put someone on it."
            if real.isEmpty {
                line += " Right now, nothing's broken or waiting on you."
                showOffer(false)
            } else {
                line += " There "
                      + (real.count == 1 ? "is one thing" : "are \(real.count) things")
                      + " worth your attention. Want to hear "
                      + (real.count == 1 ? "it" : "them") + "?"
                pendingKind = "agenda"
                style(yesBtn, title: "Yes, tell me", accent: true)
                showOffer(true)
            }
            say(line)
            UserDefaults.standard.set(true, forKey: App.introducedKey)
        } else {
            // Every launch after the first: a bubble, not a speech.
            ghost.glow = real.isEmpty ? 0.25 : 1.0
            if real.isEmpty {
                setBubble("")
                showOffer(false)
            } else {
                setBubble("\(real.count) thing\(real.count == 1 ? "" : "s") "
                          + "worth a look \u{2014} want to hear "
                          + "\(real.count == 1 ? "it" : "them")?")
                pendingKind = "agenda"
                style(yesBtn, title: "Yes, tell me", accent: true)
                showOffer(true)
            }
        }
        // What was offered counts as delivered, or the follow-up check re-says
        // item one in different words right after.
        if let b = Meditate.brief(), !b.headline.isEmpty {
            markDelivered(b.headline)
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 25) { self.check() }
    }

    /// Read the list out, then offer to act on the first one.
    func readAgenda() {
        let real = agendaItems.filter { !$0.action.isEmpty }
        guard !real.isEmpty else { say("Nothing on the list."); return }
        var line = ""
        for (i, item) in real.enumerated() {
            if real.count > 1 {
                line += (i == 0 ? "First, " : (i == 1 ? "Second, " : "Then, "))
            }
            line += item.say + " "
        }
        pendingAction = real[0].action
        pendingKind = "run"
        line += real.count == 1 ? "Want me to get on it?"
                                : "Want me to start with the first one?"
        say(line)
        style(yesBtn, title: "Yes, do it", accent: true)
        showOffer(true)
    }

    /// Poll meditate. If there's something worth saying AND you're at a pause,
    /// brighten, speak it once, and OFFER the fix as a question.
    func check() {
        DispatchQueue.global().async {
            let viaServer = Meditate.briefFromServer()
            DispatchQueue.main.async { self.serverState = viaServer != nil ? "up" : "down" }
            guard let b = viaServer ?? Meditate.brief() else { return }
            DispatchQueue.main.async {
                self.refreshFleetButton(b.fleetRunning)
                let hasSomething = !b.headline.isEmpty && b.kind != "clear"
                self.ghost.glow = hasSomething ? 1.0 : 0.25
                // Quiet mode: he may glow, he may not speak or offer.
                if self.quiet { return }
                // An offer on screen is a question already asked — a background
                // poll must never talk over it or swap what "Yes" means.
                guard self.pendingKind.isEmpty, self.yesBtn.isHidden else { return }
                // Never speak while being spoken to: keyboard idle says nothing
                // about a VOICE conversation, so the ear gets its own veto.
                if self.armed { return }
                if self.ear.running && self.ear.quietFor < 4 { return }
                guard hasSomething, b.canInterrupt,
                      !self.alreadyDelivered(b.headline),
                      !self.mouth.speaking else { return }
                self.lastSpoken = b.headline
                self.pendingAction = b.action
                self.ghost.startle()      // notice it before saying it
                let offer = b.action.isEmpty ? "" :
                    "  Want me to \(b.action.contains("fix") ? "fix it" : "get someone on it")?"
                if !b.action.isEmpty { self.pendingKind = "run" }
                self.say(b.headline + offer)
                self.showOffer(!b.action.isEmpty)
            }
        }
    }

    @objc func sayYes() {
        if pendingKind == "agenda" {          // "Yes, tell me" — read the list
            pendingKind = ""
            showOffer(false)
            readAgenda()
            return
        }
        let action = pendingAction
        pendingKind = ""
        showOffer(false)
        setBubble("On it…")
        DispatchQueue.global().async {
            let out = Meditate.perform(action)
            let lines = out.split(separator: "\n").map(String.init)
            let first = lines.first ?? "Done."
            DispatchQueue.main.async {
                // The one unprompted celebration he gets: a job you asked for,
                // finished. Tying it to anything cheaper makes it worthless.
                let failed = first.lowercased().contains("error")
                    || first.lowercased().contains("nothing to run")
                if !failed { self.ghost.celebrate() }
                self.say(first)
                // The voice gets the summary; the bubble keeps the detail —
                // which agent, doing what, in which repo. "Launched 2
                // agent(s):" with everything else thrown away was the whole
                // complaint.
                if lines.count > 1 {
                    self.setBubble(lines.prefix(5).joined(separator: "\n"))
                }
                // and now keep an eye on it, rather than starting something
                // and never mentioning it again
                if !failed {
                    self.fleetTicked.removeAll()
                    self.fleetToldStalled.removeAll()
                    self.watchFleet()
                }
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
        route(question)
    }

    /// The split that keeps the mascot safe AND conversational:
    /// CODE decides what is allowed and what runs; the LLM only decides words.
    ///
    /// The no-ship line and the command lane used to live only in converse.py
    /// — the page's route. The mascot sent everything to the advisor, so its
    /// hard line was a sentence in a prompt, and "run the fleet" got a
    /// description instead of an offer. Same sentence, two behaviours,
    /// depending on which mouth heard it.
    func route(_ question: String) {
        // One thought at a time. The advisor can take tens of seconds, and a
        // second utterance meanwhile started a SECOND advisor — two answers
        // racing for one mouth, the later overwriting the earlier.
        guard !busy else {
            setBubble("One sec \u{2014} still thinking about the last one.")
            return
        }
        switch routeDecision(question) {
        case .refuse(let line):
            say(line)
            return
        case .offer(let verb, let line):
            pendingAction = "meditate " + verb
            pendingKind = "run"
            style(yesBtn, title: "Yes, do it", accent: true)
            showOffer(true)
            say(line)
            return
        case .advise:
            break
        }

        // open question: the LLM phrases an answer over verified facts
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
        // Two different mutes, and one name for both is how a test reads the
        // wrong one: earMuted is the ear ignoring HIM while he speaks;
        // voice is the switch you press to stop him making noise at all.
        parts.append("earMuted=" + (ear.muted ? "yes" : "no"))
        parts.append("voice=" + (voiceOff ? "OFF" : "on"))
        parts.append("bar=" + (statusItem == nil ? "none"
                     : (statusItem.button == nil ? "no-button" : "ok")))
        parts.append("winVisible=" + ((window?.isVisible ?? false) ? "yes" : "no"))
        parts.append(String(format: "taskAge=%.0f", ear.taskAge))
        parts.append("earErr=" + (ear.lastError.isEmpty ? "-"
                     : String(ear.lastError.prefix(48))
                         .replacingOccurrences(of: " ", with: "_")))
        parts.append("speaking=" + (mouth.speaking ? "yes" : "no"))
        parts.append(String(format: "mouth=%.3f", Double(mouth.drive)))
        parts.append("engine=" + mouth.lane)
        parts.append("armed=" + (armed ? "yes" : "no"))
        parts.append("quiet=" + (quiet ? "on" : "off"))
        parts.append("stop=" + (stopBtn.isHidden ? "hidden" : "shown"))
        parts.append("yesMeans=" + (pendingKind.isEmpty ? "-" : pendingKind))
        parts.append("yesLabel=" + yesBtn.title.replacingOccurrences(of: " ", with: "_"))
        parts.append("agenda=" + String(agendaItems.filter { !$0.action.isEmpty }.count))
        // Which connections are actually live, checked at runtime rather than
        // by reading the source and calling it wired.
        var wired: [String] = []
        if ear.onPartial   != nil { wired.append("ear>bubble") }
        if ear.onUtterance != nil { wired.append("ear>brain") }
        if mouth.onFinish  != nil { wired.append("mouth>unmute") }
        if ghost.onClick   != nil { wired.append("click>listen") }
        if ghost.hearLevel == ear.level { wired.append("mic>face") }
        parts.append("wired=" + wired.joined(separator: ","))
        parts.append("fleet=" + (fleetBtn.isHidden ? "hidden" : "shown")
                     + ":" + fleetBtn.title + ":" + String(fleetRunning))
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
        parts.append("hotkey=" + Hotkey.shared.describe + ":" + String(Hotkey.shared.fires))
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
    static let arrivedKey = "hasArrived"

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
        style(micBtn, title: on ? "Hearing" : "Talk", accent: on)
        micBtn.toolTip = on ? "Click to stop listening"
                            : "Click to let Casper hear you"
    }

    /// Green starts the fleet, red stops it. Same button — the colour IS the
    /// state, so there is never a moment where you can press start on
    /// something already running.
    @objc func toggleFleet() {
        let stopping = fleetRunning > 0
        setBubble(stopping ? "Stopping…" : "Starting the fleet…")
        busy = true
        DispatchQueue.global().async {
            let out = Meditate.postAct(stopping ? "stopfleet" : "go") ?? ""
            DispatchQueue.main.async {
                self.busy = false
                if !stopping && !out.lowercased().contains("error") {
                    self.ghost.celebrate()
                }
                self.say(out.isEmpty ? (stopping ? "Stopped." : "They're off.")
                                     : String(out.prefix(220)))
                self.refreshFleetButton(stopping ? 0 : self.fleetRunning)
            }
        }
    }

    /// The button repaints from the SERVER's count, never from what we just
    /// asked for — otherwise it lies for the seconds between the two.
    func refreshFleetButton(_ n: Int) {
        fleetRunning = n
        if n > 0 {
            tinted(fleetBtn, title: "Stop \(n)", tint: .red)
        } else {
            tinted(fleetBtn, title: "Start", tint: .green)
        }
    }

    @objc func stopTalking() {
        mouth.shutUp()
        ear.muted = false
        setBubble("")
    }

    func showMenu(_ e: NSEvent) {
        NSMenu.popUpContextMenu(buildMenu(), with: e, for: ghost)
    }

    /// The dashboard is the place you decide what is next. Reaching it should
    /// not require remembering a port number.
    /// The hot key was pressed. Come forward, and start listening — pressing
    /// it again while listening stops, so one key is the whole conversation.
    func summoned() {
        NSApp.activate(ignoringOtherApps: true)
        window.makeKeyAndOrderFront(nil)
        ghost.bounce()
        armForOneTurn()
    }

    @objc func openDashboard() {
        if let u = URL(string: Notifier.dashboard) { NSWorkspace.shared.open(u) }
    }

    @objc func showAbout() {
        let a = NSAlert()
        a.messageText = "Casper"
        a.informativeText =
            "I keep track of what you're building — what you told me, "
            + "what's still true, and what's waiting on you.\n\n"
            + "Click me and talk, or say \"Casper, ...\". Everything I hear "
            + "is recognised on this Mac and never leaves it.\n\n"
            + "Stop ends whatever I'm saying. Right-click me for this menu, "
            + "\"Stay quiet\" if you only want me to answer when asked, "
            + "or Quit to close me entirely."
        a.addButton(withTitle: "OK")
        NSApp.activate(ignoringOtherApps: true)
        a.runModal()
    }

    /// Mute: no sound at all. The words keep arriving in the bubble, which is
    /// why this is a separate switch from `quiet` — one stops him volunteering,
    /// this one stops him making noise.
    @objc func toggleMute() {
        voiceOff.toggle()
        if voiceOff {
            mouth.shutUp()
            ear.muted = false
            setBubble("Muted. I'll still write, just not out loud.")
        } else {
            setBubble("Voice back on.")
        }
        refreshMuteButton()
    }

    func refreshMuteButton() {
        guard muteBtn != nil else { return }
        tinted(muteBtn, title: voiceOff ? "Muted" : "Mute",
               tint: voiceOff ? .red : .neutral)
    }

    @objc func toggleQuiet() {
        quiet.toggle()
        if quiet {
            mouth.shutUp()
            pendingKind = ""; pendingAction = ""
            showOffer(false)
            setBubble("Staying quiet. I'll only answer when you ask.")
        } else {
            setBubble("Back to normal — I'll mention things when they matter.")
        }
    }

    /// One menu, used by the menu bar and by right-clicking him. Two copies
    /// would drift, and the bar's copy is the one that has to work when he is
    /// nowhere on screen.
    func buildMenu() -> NSMenu {
        let m = NSMenu()
        let showing = window != nil && window.isVisible
        let show = NSMenuItem(title: showing ? "Hide Casper" : "Show Casper",
                              action: #selector(toggleVisible), keyEquivalent: "")
        show.target = self
        m.addItem(show)
        m.addItem(NSMenuItem.separator())
        m.addItem(withTitle: "Open dashboard", action: #selector(openDashboard),
                  keyEquivalent: "").target = self
        m.addItem(withTitle: "About Casper", action: #selector(showAbout),
                  keyEquivalent: "").target = self
        m.addItem(NSMenuItem.separator())
        let mute = NSMenuItem(title: "Mute (no voice, still writes)",
                              action: #selector(toggleMute), keyEquivalent: "")
        mute.target = self; mute.state = voiceOff ? .on : .off
        m.addItem(mute)
        let q = NSMenuItem(title: "Stay quiet (only answer when asked)",
                           action: #selector(toggleQuiet), keyEquivalent: "")
        q.target = self; q.state = quiet ? .on : .off
        m.addItem(q)
        m.addItem(NSMenuItem.separator())
        m.addItem(withTitle: "Quit Casper", action: #selector(quitCasper),
                  keyEquivalent: "q").target = self
        return m
    }

    /// The menu bar is the only thing that survives closing him.
    ///
    /// He is LSUIElement, so there is no Dock icon either — closing the window
    /// used to call NSApp.terminate and the only way back was typing
    /// `meditate casper` in a terminal. A companion you cannot get back from
    /// the screen you lost him on is a companion you stop using.
    func installMenuBar() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        if let b = statusItem.button {
            b.image = App.barIcon()
            b.image?.isTemplate = true          // follows light/dark menu bars
            b.toolTip = "Casper"
        }
        statusItem.menu = buildMenu()
    }

    /// His own face, small. Drawn rather than shipped as an asset so it can
    /// never go missing from the bundle.
    static func barIcon() -> NSImage {
        let s = NSSize(width: 16, height: 16)
        let img = NSImage(size: s)
        img.lockFocus()
        let p = NSBezierPath()
        let w = s.width, h = s.height
        p.move(to: NSPoint(x: 1.5, y: 2))
        p.curve(to: NSPoint(x: w / 2, y: h - 1),
                controlPoint1: NSPoint(x: 1.5, y: h * 0.75),
                controlPoint2: NSPoint(x: w * 0.18, y: h - 1))
        p.curve(to: NSPoint(x: w - 1.5, y: 2),
                controlPoint1: NSPoint(x: w * 0.82, y: h - 1),
                controlPoint2: NSPoint(x: w - 1.5, y: h * 0.75))
        // three little hem lobes, same silhouette as the big one
        p.curve(to: NSPoint(x: w / 2, y: 2.5),
                controlPoint1: NSPoint(x: w - 1.5, y: 0.2),
                controlPoint2: NSPoint(x: w * 0.68, y: 0.2))
        p.curve(to: NSPoint(x: 1.5, y: 2),
                controlPoint1: NSPoint(x: w * 0.32, y: 0.2),
                controlPoint2: NSPoint(x: 1.5, y: 0.2))
        p.close()
        NSColor.black.setFill(); p.fill()
        NSColor.white.setFill()
        NSBezierPath(ovalIn: NSRect(x: w * 0.32, y: h * 0.52, width: 2.4, height: 3)).fill()
        NSBezierPath(ovalIn: NSRect(x: w * 0.56, y: h * 0.52, width: 2.4, height: 3)).fill()
        img.unlockFocus()
        return img
    }

    /// When he has nothing to say, say what he is WAITING FOR.
    ///
    /// Three ways in already existed — click him, press the hotkey, or say his
    /// name — and none of them were written anywhere on screen. "It is not
    /// clear whether it will wait for my command or whether I have to click"
    /// is the correct reading of a face that shows only the last thing it
    /// said. An affordance nobody can see is not an affordance.
    func idleHint() {
        guard window != nil, window.isVisible else { return }
        guard !mouth.speaking, !busy, yesBtn.isHidden else { return }
        guard bubbleIsStale() else { return }
        if !micEnabled {
            setBubble("Ask me anything \u{2014} or turn my mic on to just talk.")
        } else if armed {
            setBubble("Listening. Say what you like \u{2014} click me to stop.")
        } else {
            setBubble("Say \u{201C}Casper\u{2026}\u{201D}, click me, or press "
                      + Hotkey.shared.describe + " to talk.")
        }
    }

    /// True when the bubble has been sitting on the same words long enough
    /// that it is no longer news.
    func bubbleIsStale() -> Bool {
        Date().timeIntervalSince(lastBubbleAt) > 25
    }

    /// Grow upward for the fleet, so the buttons never move under your cursor.
    /// The window is anchored bottom-right; adding height at the top is the
    /// only change that does not shift what you were about to click.
    func layoutFleet(_ rows: [FleetView.Row]) {
        fleetView.rows = rows
        let h = FleetView.height(for: rows.count)
        guard h != fleetH else { return }
        fleetH = h
        let W = window.frame.width
        var f = window.frame
        let baseH: CGFloat = 248
        f.size.height = baseH + h
        window.setFrame(f, display: true, animate: false)
        let rootH = f.size.height
        window.contentView?.frame = NSRect(x: 0, y: 0, width: W, height: rootH)
        fleetView.frame = NSRect(x: 10, y: 110, width: W - 20, height: h)
        ghost.frame = NSRect(x: (W - 132) / 2, y: 110 + h, width: 132, height: 132)
        // The X is pinned to the top-right corner, and the corner moved.
        closeBtn.frame = NSRect(x: W - 24, y: rootH - 24, width: 18, height: 18)
    }

    /// Take me to it. The next thing you want after "what did I launch" is
    /// "show me the one that is stuck".
    func showAgent(_ r: FleetView.Row) {
        guard !r.window.isEmpty else {
            setBubble("I don't have a window for \(pretty(r.goal)) — it was "
                      + "started before I began recording them.")
            return
        }
        let script = "tell application \"Terminal\"\n"
            + "  set index of window id \(r.window) to 1\n"
            + "  activate\n"
            + "end tell"
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
        p.arguments = ["-e", script]
        p.standardError = FileHandle.nullDevice
        try? p.run()
        setBubble(pretty(r.goal) + " — brought its window to the front.")
    }

    @objc func toggleVisible() {
        guard window != nil else { return }
        if window.isVisible {
            window.orderOut(nil)
        } else {
            window.makeKeyAndOrderFront(nil)
        }
        statusItem?.menu = buildMenu()          // the label flips with the state
    }

    @objc func quitCasper() { NSApp.terminate(nil) }

    /// Starting an agent and then forgetting about it is the thing that makes
    /// a fleet feel like shouting into a room. He watches what he started and
    /// tells you once when it lands, or once when it clearly has not.
    var fleetTicked: [String: Bool] = [:]
    var fleetToldStalled: Set<String> = []
    /// Measured on this machine: real agents ran 26, 47, 47 and 50 minutes
    /// while still working. Fifteen minutes was not a stall, it was normal
    /// work — so four of six jobs tripped it and he asked four times.
    static let stallMinutes = 45

    func watchFleet() {
        DispatchQueue.global().async {
            let rows = Meditate.fleet()
            DispatchQueue.main.async {
                // Show what is out there, always — even when nothing has
                // changed, and even when there is nothing else to say.
                self.layoutFleet(rows.map {
                    FleetView.Row(goal: self.pretty($0.goal), ticked: $0.ticked,
                                  mins: $0.mins, window: $0.window)
                })
            }
            guard !rows.isEmpty else { return }
            DispatchQueue.main.async {
                // LANDED first — good news interrupts, bad news waits.
                for r in rows where self.fleetTicked[r.goal] == false && r.ticked {
                    self.fleetTicked[r.goal] = true
                    let name = self.pretty(r.goal)
                    let done = rows.filter { $0.ticked }
                    self.ghost.celebrate()
                    var line = self.landedLine(name, of: done.count)

                    // ...and OFFER to tidy up. He used to announce a finished
                    // job and return — no button, no way to say yes. Telling
                    // someone a thing is done and asking whether to clear the
                    // board, with nothing to press, is worse than not asking.
                    if !self.quiet {
                        self.pendingAction = "clear"       // every finished row
                        self.pendingKind = "run"
                        self.style(self.yesBtn,
                                   title: done.count > 1 ? "Clear them" : "Clear it",
                                   accent: true)
                        self.showOffer(true)
                        line += done.count > 1
                            ? " Want me to take those \(done.count) off the board?"
                            : " Want me to take it off the board?"
                    }
                    self.say(line)
                    Notifier.shared.post(title: name + " is done",
                                         body: "Click to open the dashboard.")
                    return
                }
                for r in rows { self.fleetTicked[r.goal] = r.ticked }

                // SLOW: asked about ONCE, for all of them together.
                //
                // This used to fire per goal. With four jobs past the
                // threshold you got four separate questions, one every twenty
                // seconds — which is how a helpful nudge becomes nagging.
                let slow = rows.filter { !$0.ticked && $0.mins >= App.stallMinutes }
                let fresh = slow.filter { !self.fleetToldStalled.contains($0.goal) }
                guard !fresh.isEmpty, !self.quiet else { return }
                fresh.forEach { self.fleetToldStalled.insert($0.goal) }

                let longest = slow.max(by: { $0.mins < $1.mins })!
                self.pendingAction = fresh.count == 1
                    ? "clear " + fresh[0].goal : "clear"
                self.pendingKind = "stall"
                self.style(self.yesBtn, title: "Stop tracking", accent: true)
                self.showOffer(true)
                self.say(self.slowLine(fresh.count, longest: self.pretty(longest.goal),
                                       mins: longest.mins))
            }
        }
    }

    /// Deterministic variety: the same news gets the same words, different
    /// news gets different words. Random phrasing would make him re-word an
    /// identical fact every twenty seconds, which reads as instability.
    func vary(_ options: [String], _ seed: String) -> String {
        var h = 5381
        for b in seed.utf8 { h = (h &* 33) &+ Int(b) }
        return options[abs(h) % options.count]
    }

    func landedLine(_ name: String, of total: Int) -> String {
        if total >= 3 {
            return vary(["\(name) is done. That's \(total) finished now.",
                         "\(name) just landed — \(total) done.",
                         "And \(name)'s in. \(total) of them now."], name)
        }
        return vary(["\(name) just landed.",
                     "\(name) is done.",
                     "That's \(name) finished."], name)
    }

    /// Says what it MEANS. "Drop it from the list" told you nothing: not what
    /// list, not whether the agent dies with it. It does not — the agent keeps
    /// working in its own window; this only stops me watching for it.
    func slowLine(_ n: Int, longest: String, mins: Int) -> String {
        let body = n == 1
            ? "\(longest) has been going \(mins) minutes and hasn't ticked anything off."
            : "\(n) jobs are past \(App.stallMinutes) minutes with nothing ticked — "
              + "\(longest) the longest at \(mins)."
        return body + " Want me to stop tracking "
             + (n == 1 ? "it" : "them")
             + "? They keep running either way — it just clears my board."
    }

    /// "goal-mila-unblocked" is a key. "Mila unblocked" is a thing you said.
    func pretty(_ name: String) -> String {
        var s = name
        for p in ["goal-", "goal_"] where s.hasPrefix(p) { s = String(s.dropFirst(p.count)) }
        s = s.replacingOccurrences(of: "-", with: " ")
             .replacingOccurrences(of: "_", with: " ")
        return s.prefix(1).uppercased() + s.dropFirst()
    }

    func resetOfferButton() {
        style(yesBtn, title: "Yes, fix it", accent: true)
    }

    /// The X hides him. It used to terminate, which — with no Dock icon and
    /// no menu bar — meant the only way back was a terminal command.
    @objc func closeCasper() {
        mouth.shutUp()
        window.orderOut(nil)
        statusItem?.menu = buildMenu()
    }

    @objc func sayNo() {
        pendingKind = ""
        pendingAction = ""
        showOffer(false)
        say("Alright \u{2014} I'll leave it.")
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
