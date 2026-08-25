// AppleModel.swift — the model that is already on the machine.
//
// Why this exists at all is a distribution question, not a quality one.
//
// The alternative was pulling a 14B model through ollama: about 9 GB of
// download, 9 GB of disk that never comes back, and several GB of RAM
// resident while the owner is trying to work. On this machine that was not
// even affordable — 14 GB free of 460 GB when it was checked, so the pull
// would have left about 5 GB. And every person who ever installed this tool
// would have to do the same thing before the companion could think.
//
// Apple's on-device model ships with the OS. Nothing to download, nothing to
// keep, nothing to ask the user to install. Measured on this M1 Pro, macOS
// 26.5: first call 6.57s while the model loads, then 0.95s and 1.09s — the
// same ballpark as the local 4B it replaces, for zero bytes.
//
// It is NOT available everywhere, which is the honest half of the answer:
// Apple silicon only, Apple Intelligence has to be switched on, and the
// model has to have finished downloading. All three failures are reported
// separately by availability(), and each one is a reason to fall back rather
// than an error — see advisor.py, where ollama and then the deterministic
// layer catch what this cannot serve.
#if canImport(FoundationModels)
import FoundationModels
#endif
import Foundation

enum AppleModel {

    /// "available", or a plain-words reason it is not.
    static func availability() -> String {
        #if canImport(FoundationModels)
        guard #available(macOS 26.0, *) else {
            return "needs macOS 26 or newer"
        }
        switch SystemLanguageModel.default.availability {
        case .available:
            return "available"
        case .unavailable(let why):
            switch why {
            case .deviceNotEligible:
                return "this Mac cannot run it (Apple silicon only)"
            case .appleIntelligenceNotEnabled:
                return "Apple Intelligence is switched off in System Settings"
            case .modelNotReady:
                return "the model is still downloading"
            @unknown default:
                return "unavailable"
            }
        @unknown default:
            return "unavailable"
        }
        #else
        return "built without FoundationModels"
        #endif
    }

    /// Answer `prompt`, handing back each finished sentence as it arrives.
    ///
    /// Same shape as advisor.py's --stream: one sentence per line, flushed,
    /// so the mouth can start speaking before the thought is finished. The
    /// snapshots this framework yields are CUMULATIVE — each one is the whole
    /// answer so far, not the new piece — so what has already been spoken has
    /// to be tracked, or every sentence is said once per token.
    ///
    /// Returns false when the model could not answer at all, so the caller
    /// knows to fall back rather than to report silence as an answer.
    @discardableResult
    static func stream(_ prompt: String,
                       onSentence: @escaping (String) -> Void) -> Bool {
        #if canImport(FoundationModels)
        guard #available(macOS 26.0, *),
              case .available = SystemLanguageModel.default.availability
        else { return false }

        var ok = false
        let sem = DispatchSemaphore(value: 0)
        Task {
            let session = LanguageModelSession()
            var emitted = 0
            do {
                let stream = session.streamResponse(to: prompt)
                var whole = ""
                for try await snapshot in stream {
                    whole = snapshot.content
                    let done = Mouth.sentences(whole)
                    // The last one is still being written unless the text
                    // ends on a stop, so hold it back.
                    let ready = whole.hasSuffix(".") || whole.hasSuffix("!")
                             || whole.hasSuffix("?") ? done : Array(done.dropLast())
                    while emitted < ready.count {
                        onSentence(ready[emitted])
                        emitted += 1
                    }
                }
                let all = Mouth.sentences(whole)
                while emitted < all.count {
                    onSentence(all[emitted])
                    emitted += 1
                }
                ok = emitted > 0
            } catch {
                ok = false
            }
            sem.signal()
        }
        // Generous, because the FIRST call on a cold machine loads the model.
        _ = sem.wait(timeout: .now() + 60)
        return ok
        #else
        return false
        #endif
    }
}
