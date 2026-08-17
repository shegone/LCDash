// MAE avatar runtime -- Phase 1 of docs/planning/MAE_AVATAR_PLAN_2026-08-09.md.
//
// Two renderers, one driver. Until the Character Creator export exists, MAE
// is her actual portrait photographs (static/img/mae/), animated in a 2D
// canvas: the jaw region translates with the same viseme-driven weights that
// will one day drive real morph targets, eyelids blink, and the soft-smile
// portrait cross-fades in for warmth. When /static/models/mae.glb appears,
// the three.js renderer takes over and drives its ARKit morph targets from
// the very same weight names. Rendering is always local -- no server GPU.

import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { DRACOLoader } from "three/addons/loaders/DRACOLoader.js";

(function () {
    "use strict";

    const cloudMode = document.body.dataset.cloudMode === "true";
    const stage = document.getElementById("avatar-stage");
    const fallback = document.getElementById("portrait-fallback");
    const transcript = document.getElementById("transcript");
    const questionInput = document.getElementById("question");
    const sendButton = document.getElementById("send");
    const pttButton = document.getElementById("ptt");
    const stopButton = document.getElementById("stop-speech");
    const identityPill = document.getElementById("pill-identity");
    const voicePill = document.getElementById("pill-voice");

    const params = new URLSearchParams(window.location.search);
    if (params.get("kiosk") === "1") document.body.classList.add("kiosk");

    // ------------------------------------------------------------------
    // Rapport.cloud avatar (MAE_RAPPORT_ENABLED, app/config/settings.py).
    // Detected once here, not re-queried per utterance: when the flag is
    // off, templates/mae_avatar.html emits no #rapport-stage at all, so
    // rapportElement is null and every rapport.* call below is a single
    // boolean check with no DOM query, no fetch, no behavior change --
    // this page must stay byte-for-byte the page it is today unless the
    // flag is on.
    // ------------------------------------------------------------------

    const rapportElement = document.getElementById("rapport-scene");

    const rapport = (function () {
        if (!rapportElement) {
            return { isActive: function () { return false; }, speak: function () { return false; }, stop: function () {} };
        }

        // Per-utterance, not a load-time constant: a session that is still
        // negotiating (or that drops mid-conversation) must fall back to
        // the local voice+viseme path for the NEXT reply, not leave MAE
        // silent until Rapport reconnects on its own schedule.
        let connected = false;

        try {
            // sessionDisconnected is not documented in the Integrate
            // sample either way; passing it is harmless if unsupported,
            // and if their component DOES call it, a dropped session
            // flips the very next reply back to local playback instead
            // of speak() discovering the corpse one failed sendText later.
            const request = rapportElement.sessionRequest({
                sessionConnected: function () {
                    connected = true;
                },
                sessionDisconnected: function () {
                    connected = false;
                    console.warn("MAE avatar: Rapport session ended; using the local avatar until it returns.");
                }
            });
            // Their sample treats sessionRequest as fire-and-forget, but
            // if it returns a promise, an async rejection would otherwise
            // surface as an unhandled-rejection console error with no
            // fallback bookkeeping attached to it.
            if (request && typeof request.catch === "function") {
                request.catch(function (error) {
                    connected = false;
                    console.warn("MAE avatar: Rapport session failed to start; using the local avatar instead.", error);
                });
            }
        } catch (error) {
            // This project has been burned by silent failure modes all
            // week -- a Rapport session failing to start must be loud in
            // the console and invisible in behavior. connected stays
            // false, so speak() below is never even attempted and the
            // existing three.js/portrait renderer chain does exactly what
            // it does when this flag does not exist.
            console.warn("MAE avatar: Rapport session failed to start; using the local avatar instead.", error);
        }

        function isActive() {
            return connected;
        }

        // Returns whether Rapport actually took the utterance. Callers must
        // fall back to local synthesis+playback when this returns false --
        // an utterance rejected here would otherwise be spoken nowhere.
        function speak(text) {
            try {
                rapportElement.modules.tts.sendText(text);
                return true;
            } catch (error) {
                console.warn("MAE avatar: Rapport sendText failed; falling back to local speech for this reply.", error);
                connected = false; // fail closed: later utterances skip straight to local playback too
                return false;
            }
        }

        function stop() {
            // Rapport's own Integrate-dialog embed sample exposes
            // scene.modules.tts.sendText() but documents no stop/cancel
            // call anywhere in it. Feature-detect a handful of plausible
            // names defensively; if none exist, the Stop button silences
            // only OUR audio (see stopButton handler below) and Rapport
            // keeps talking -- a known, documented limitation, not a bug.
            try {
                const tts = rapportElement.modules && rapportElement.modules.tts;
                if (!tts) return;
                if (typeof tts.stop === "function") tts.stop();
                else if (typeof tts.cancel === "function") tts.cancel();
                else if (typeof tts.interrupt === "function") tts.interrupt();
            } catch (error) {
                console.warn("MAE avatar: Rapport stop failed.", error);
            }
        }

        return { isActive: isActive, speak: speak, stop: stop };
    })();

    // ------------------------------------------------------------------
    // Blendshape state. ARKit names only -- this is the portable contract.
    // ------------------------------------------------------------------

    const weights = Object.create(null);
    function setWeight(name, value) {
        weights[name] = Math.max(0, Math.min(1, value));
    }
    function weight(name) {
        return weights[name] || 0;
    }

    // Polly viseme -> ARKit weight targets. Everything in MOUTH_KEYS decays
    // to zero unless the active viseme names it, so shapes never stick.
    const VISEME_TARGETS = {
        sil: {},
        p: { mouthClose: 0.85, mouthPressLeft: 0.5, mouthPressRight: 0.5 },
        t: { jawOpen: 0.16 },
        S: { jawOpen: 0.14, mouthFunnel: 0.65 },
        T: { jawOpen: 0.2, tongueOut: 0.35 },
        f: { jawOpen: 0.1, mouthRollLower: 0.7 },
        k: { jawOpen: 0.22 },
        i: { jawOpen: 0.14, mouthSmileLeft: 0.45, mouthSmileRight: 0.45 },
        r: { jawOpen: 0.16, mouthPucker: 0.45 },
        s: { jawOpen: 0.1, mouthSmileLeft: 0.2, mouthSmileRight: 0.2 },
        u: { jawOpen: 0.16, mouthPucker: 0.85, mouthFunnel: 0.3 },
        "@": { jawOpen: 0.28 },
        a: { jawOpen: 0.5 },
        e: { jawOpen: 0.34, mouthSmileLeft: 0.18, mouthSmileRight: 0.18 },
        E: { jawOpen: 0.3, mouthFunnel: 0.2 },
        o: { jawOpen: 0.36, mouthFunnel: 0.55, mouthPucker: 0.3 },
        O: { jawOpen: 0.46, mouthFunnel: 0.5, mouthPucker: 0.25 }
    };
    const MOUTH_KEYS = [
        "jawOpen", "mouthClose", "mouthFunnel", "mouthPucker",
        "mouthSmileLeft", "mouthSmileRight", "mouthPressLeft",
        "mouthPressRight", "mouthRollLower", "tongueOut"
    ];

    // Models baked from Sumerian Hosts carry one morph PER Polly viseme
    // (viseme_p, viseme_a, ...) -- an exact pose per code, no ARKit
    // approximation. Each Polly code drives its own morph directly; the two
    // vocabularies coexist in VISEME_TARGETS and the draw loop applies
    // whichever names the loaded model actually has.
    for (const code of Object.keys(VISEME_TARGETS)) {
        if (code !== "sil") VISEME_TARGETS[code]["viseme_" + code] = 1;
    }
    const NATIVE_VISEME_KEYS = Object.keys(VISEME_TARGETS)
        .filter(function (code) { return code !== "sil"; })
        .map(function (code) { return "viseme_" + code; });
    const DRIVEN_MOUTH_KEYS = MOUTH_KEYS.concat(NATIVE_VISEME_KEYS);

    // Current speech playback state read by the animation loop.
    const speechState = {
        audio: null,
        visemes: [],
        cursor: 0
    };

    function isSpeaking() {
        const audio = speechState.audio;
        return Boolean(audio && !audio.paused && !audio.ended);
    }

    // Co-articulation window: how far ahead of a mark's own time the mouth
    // starts blending toward it. The VALUE is still unmeasured -- it is the
    // same 60 ms guess the old fixed lookahead used, and we have not
    // measured the right one. What changed is HOW it is used: blended
    // proportionally across this window as the audio clock approaches the
    // next mark, instead of shifting the whole timeline forward by a fixed
    // amount. Shifting the timeline made every shape arrive uniformly 60 ms
    // early; blending makes a shape begin early and still land ON TIME at
    // its mark. Reusing an unmeasured constant as a blend window rather
    // than a timeline shift is strictly less wrong, not a claim that 60 ms
    // is correct.
    const COARTICULATION_WINDOW_MS = 60;

    function blendVisemeTargets(from, to, fraction) {
        const blended = Object.create(null);
        const keys = new Set(Object.keys(from).concat(Object.keys(to)));
        keys.forEach(function (key) {
            const a = from[key] || 0;
            const b = to[key] || 0;
            blended[key] = a + (b - a) * fraction;
        });
        return blended;
    }

    function activeVisemeTargets() {
        if (!isSpeaking()) return VISEME_TARGETS.sil;
        // True audio clock -- no fixed offset. Shapes are selected for the
        // mark that has actually started, not one 60 ms in the future.
        const now = speechState.audio.currentTime * 1000;
        const marks = speechState.visemes;
        while (
            speechState.cursor + 1 < marks.length &&
            marks[speechState.cursor + 1].time_ms <= now
        ) {
            speechState.cursor += 1;
        }
        const mark = marks[speechState.cursor];
        if (!mark || mark.time_ms > now) return VISEME_TARGETS.sil;
        const current = VISEME_TARGETS[mark.viseme] || VISEME_TARGETS.sil;

        // Co-articulation: once within COARTICULATION_WINDOW_MS of the next
        // mark, blend from the current shape toward it so the mouth is
        // already moving before the next sound lands, rather than snapping
        // to it late (or, as the old code did, uniformly early).
        const nextMark = marks[speechState.cursor + 1];
        if (!nextMark) return current;
        const untilNext = nextMark.time_ms - now;
        if (untilNext > COARTICULATION_WINDOW_MS) return current;
        const next = VISEME_TARGETS[nextMark.viseme] || VISEME_TARGETS.sil;
        const fraction = Math.max(0, Math.min(1, 1 - untilNext / COARTICULATION_WINDOW_MS));
        return blendVisemeTargets(current, next, fraction);
    }

    // ------------------------------------------------------------------
    // Amplitude-driven mouth gain: louder speech opens the mouth wider,
    // quiet or trailing-off speech opens it less, so loudness reads on her
    // face instead of the mouth opening identically for every syllable.
    // Every failure mode here (no Web Audio, CORS-tainted audio, a throw
    // building the graph, a context stuck suspended) must fall back to
    // gain exactly 1.0 -- today's behaviour -- and must never throw into
    // the animation loop.
    // ------------------------------------------------------------------

    // Gain stays inside a modest band, floored so a quiet moment never
    // fully closes an opening shape and ceilinged so emphasis never blows
    // the mouth open. Centred on 1.0 for average speech level.
    const AMPLITUDE_GAIN_MIN = 0.75;
    const AMPLITUDE_GAIN_MAX = 1.25;
    // RMS (of a byte time-domain sample, 0..1 scale) treated as "average"
    // speech loudness, used to normalize the analyser reading onto the
    // gain band above. Tuned by ear, not measured -- same caveat as
    // COARTICULATION_WINDOW_MS: a placeholder, not a calibrated value.
    const AMPLITUDE_REFERENCE_RMS = 0.18;

    // viseme_p and its ARKit equivalents are lip-CLOSURE shapes, not
    // openings -- scaling them down by loudness would make a quiet "p"
    // fail to close the lips, a visible, wrong-looking regression. They
    // are driven at their authored weight regardless of amplitude.
    const AMPLITUDE_EXEMPT_KEYS = new Set([
        "viseme_p", "mouthClose", "mouthPressLeft", "mouthPressRight"
    ]);

    // One AnalyserNode per HTMLAudioElement -- createMediaElementSource()
    // throws if called twice on the same element. play() below creates a
    // fresh Audio object per utterance/chunk, so the map just grows and
    // its entries fall away naturally as old elements are garbage
    // collected; nothing here needs to evict them.
    const amplitudeNodesByElement = new WeakMap();
    let audioCtx = null;
    let audioCtxUnavailable = false;

    function getAudioCtx() {
        if (audioCtx || audioCtxUnavailable) return audioCtx;
        try {
            const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
            audioCtx = new AudioContextCtor();
        } catch (error) {
            audioCtxUnavailable = true;
            audioCtx = null;
        }
        return audioCtx;
    }

    // Called once per Audio element, right after it is assigned to
    // speechState.audio, so playback is analysed from the start.
    function attachAmplitudeAnalyser(audioEl) {
        let source = null;
        try {
            const ctx = getAudioCtx();
            if (!ctx) return null;
            // Do NOT capture the element unless the context is actually
            // running. createMediaElementSource() reroutes the element's
            // audio through the graph permanently, so capturing it into a
            // SUSPENDED context and then failing to resume leaves her
            // mute -- and the mouth would look fine the whole time, since
            // currentAmplitudeGain() correctly falls back to 1.0. Skipping
            // the capture keeps playback on the plain element: no
            // amplitude scaling for this utterance, but sound. Autoplay
            // policy usually leaves the first context suspended, so this
            // is the normal path on the first utterance, not an edge case.
            if (ctx.state !== "running") return null;
            if (amplitudeNodesByElement.has(audioEl)) {
                return amplitudeNodesByElement.get(audioEl);
            }
            source = ctx.createMediaElementSource(audioEl);
            const analyser = ctx.createAnalyser();
            analyser.fftSize = 256;
            // MUST stay connected to destination -- createMediaElementSource
            // captures the element's audio for the Web Audio graph, and an
            // element routed through the graph but not wired back to
            // destination goes silent. A silent MAE is a far worse
            // regression than a flat mouth, so this connection matters more
            // than the analyser itself.
            source.connect(analyser);
            analyser.connect(ctx.destination);
            const entry = { analyser: analyser, buffer: new Uint8Array(analyser.fftSize) };
            amplitudeNodesByElement.set(audioEl, entry);
            return entry;
        } catch (error) {
            console.warn("MAE avatar: amplitude analysis unavailable; mouth gain stays flat.", error);
            // If the source was captured but wiring the analyser failed
            // partway through, reconnect it straight to destination so
            // speech is never lost even though amplitude scaling is not
            // available for this element.
            if (source && audioCtx) {
                try { source.connect(audioCtx.destination); } catch (fallbackError) { /* best effort */ }
            }
            return null;
        }
    }

    // Resume must happen on the same user gesture that starts playback, or
    // autoplay policy leaves the context suspended and the analyser reads
    // silence forever. If it cannot be resumed, currentAmplitudeGain()
    // below falls back to 1.0 on its own.
    function resumeAudioCtxOnGesture() {
        const ctx = getAudioCtx();
        if (ctx && ctx.state === "suspended") {
            ctx.resume().catch(function () { /* stays suspended; gain falls back to 1.0 */ });
        }
    }

    function currentAmplitudeGain() {
        try {
            const audioEl = speechState.audio;
            if (!audioEl || !audioCtx || audioCtx.state !== "running") return 1.0;
            const entry = amplitudeNodesByElement.get(audioEl);
            if (!entry) return 1.0;
            entry.analyser.getByteTimeDomainData(entry.buffer);
            let sumSquares = 0;
            for (let i = 0; i < entry.buffer.length; i++) {
                const sample = (entry.buffer[i] - 128) / 128;
                sumSquares += sample * sample;
            }
            const rms = Math.sqrt(sumSquares / entry.buffer.length);
            const normalized = rms / AMPLITUDE_REFERENCE_RMS;
            const gain = 1.0 + (normalized - 1) * (AMPLITUDE_GAIN_MAX - 1.0);
            return Math.max(AMPLITUDE_GAIN_MIN, Math.min(AMPLITUDE_GAIN_MAX, gain));
        } catch (error) {
            return 1.0;
        }
    }

    // ------------------------------------------------------------------
    // Idle motion: blink, gaze wander, breathing sway. Never a statue.
    // ------------------------------------------------------------------

    const idle = {
        nextBlinkAt: performance.now() + 2400,
        blinkStartedAt: 0,
        gaze: { x: 0, y: 0 },
        gazeTarget: { x: 0, y: 0 },
        nextGazeAt: 0,
        smile: 0.3
    };

    function idleStep(now) {
        // Blink: ~140 ms close-open, every 2-6 s.
        if (now >= idle.nextBlinkAt) {
            idle.blinkStartedAt = now;
            idle.nextBlinkAt = now + 2000 + Math.random() * 4000;
        }
        const blinkT = (now - idle.blinkStartedAt) / 140;
        const blink = blinkT < 1 ? Math.sin(Math.PI * blinkT) : 0;
        setWeight("eyeBlinkLeft", blink);
        setWeight("eyeBlinkRight", blink);
        // Sumerian Host bakes carry one combined "blink" morph instead of
        // ARKit's per-eye pair; the draw loop skips names a model lacks, so
        // driving both vocabularies costs nothing.
        setWeight("blink", blink);

        // Gaze: small saccades toward a wandering target near the viewer.
        if (now >= idle.nextGazeAt) {
            idle.gazeTarget.x = (Math.random() - 0.5) * 0.5;
            idle.gazeTarget.y = (Math.random() - 0.5) * 0.24;
            idle.nextGazeAt = now + 900 + Math.random() * 2600;
        }
        idle.gaze.x += (idle.gazeTarget.x - idle.gaze.x) * 0.12;
        idle.gaze.y += (idle.gazeTarget.y - idle.gaze.y) * 0.12;

        // Warmth: a slow soft-smile while listening, mostly neutral while
        // talking so the mouth animation stays legible.
        const smileTarget = isSpeaking()
            ? 0.08
            : 0.28 + Math.sin(now / 4700) * 0.16;
        idle.smile += (smileTarget - idle.smile) * 0.03;

        setWeight("browInnerUp", isSpeaking() ? 0.18 : 0.06);
    }

    // ------------------------------------------------------------------
    // Portrait renderer: MAE's photographs, animated on a 2D canvas.
    // ------------------------------------------------------------------

    // Two source figures, because the two frames want genuinely different
    // pictures: the console wants her face, the trade-show panel wants a
    // life-size standing person. Landmarks are normalized to each source
    // image. Every value was measured against actual pixels -- if a
    // reference image is re-shot or re-cropped, re-measure rather than
    // nudging numbers until it looks right.
    //
    // `band` is the vertical slice of the source that fills the pane's
    // height; the sides letterbox against the page background on wide
    // screens. (Multiplying a zoom onto cover-fit instead meant a widescreen
    // monitor showed only her eyes.)
    const FIGURES = {
        // Head-and-shoulders portraits, 832x1248.
        portrait: {
            src: "/static/img/mae/mae-neutral.jpg",
            smileSrc: "/static/img/mae/mae-soft-smile.jpg",
            face: { cx: 0.469, cy: 0.399 },
            mouth: { x0: 0.397, x1: 0.575, lip: 0.561 },
            jaw: { x0: 0.33, x1: 0.66, top: 0.558, bottom: 0.665, maxDrop: 0.019 },
            eyes: [
                { x: 0.300, y: 0.377, w: 0.097, h: 0.046 },
                { x: 0.547, y: 0.377, w: 0.097, h: 0.046 }
            ],
            smileRegion: { x0: 0.28, x1: 0.70, y0: 0.43, y1: 0.68 },
            band: { top: 0.13, bottom: 0.86 }
        },
        // Full-body A-pose, derived from the 2026-08-11 reference export with
        // its white background keyed out, so she stands against the page
        // rather than on a white slab. Her head is a small fraction of this
        // frame, so the mouth displacement is correspondingly tiny -- at
        // booth distance the readable signal is that she moves at all.
        // Landmarks are normalized to the DERIVED asset (760x1139, cropped to
        // her silhouette), not to the 1152x1728 source they were measured on
        // -- cropping moves every normalized coordinate. Rebuilding the asset
        // with a different crop means recomputing these.
        fullBody: {
            src: "/static/img/mae/mae-fullbody.png",
            smileSrc: "",
            face: { cx: 0.4954, cy: 0.0898 },
            mouth: { x0: 0.4808, x1: 0.5114, lip: 0.1130 },
            jaw: { x0: 0.4582, x1: 0.5345, top: 0.1066, bottom: 0.1361, maxDrop: 0.0043 },
            eyes: [
                { x: 0.4392, y: 0.0628, w: 0.0295, h: 0.0135 },
                { x: 0.4926, y: 0.0620, w: 0.0295, h: 0.0135 }
            ],
            smileRegion: null,
            band: { top: 0.0, bottom: 1.0 }
        }
    };
    const FRAME_FIGURES = { console: "portrait", booth: "fullBody" };
    let activeFrame = "console";

    function markActiveFramePill() {
        document.getElementById("pill-frame-console").classList.toggle(
            "frame-active", activeFrame === "console");
        document.getElementById("pill-frame-booth").classList.toggle(
            "frame-active", activeFrame === "booth");
    }

    function createPortraitRenderer() {
        const canvas = document.createElement("canvas");
        canvas.style.cssText = "display:block;width:100%;height:100%;";
        stage.appendChild(canvas);
        const ctx = canvas.getContext("2d");

        // One entry per figure: its loaded images, or null while loading.
        const loaded = Object.create(null);

        function loadImage(src) {
            return new Promise(function (resolve, reject) {
                const img = new Image();
                img.onload = function () { resolve(img); };
                img.onerror = reject;
                img.src = src;
            });
        }

        // The portrait must load or there is nothing to draw; the full-body
        // figure is allowed to fail on its own, in which case the booth
        // frame falls back to the portrait rather than the page going blank.
        const ready = Promise.all([
            loadImage(FIGURES.portrait.src),
            loadImage(FIGURES.portrait.smileSrc)
        ]).then(function (images) {
            loaded.portrait = { base: images[0], smile: images[1] };
            // First paint immediately: rAF is throttled or paused in hidden
            // or backgrounded panes, and she should be there the moment the
            // page becomes visible rather than one frame later.
            draw(performance.now());
        }).catch(function () {
            // Static portrait fallback: still MAE, just not animated.
            canvas.remove();
            fallback.style.display = "flex";
        });

        loadImage(FIGURES.fullBody.src).then(function (img) {
            loaded.fullBody = { base: img, smile: null };
        }).catch(function () {
            console.info("MAE avatar: full-body figure unavailable; booth frame uses the portrait.");
        });

        function resize() {
            const dpr = Math.min(window.devicePixelRatio || 1, 2);
            canvas.width = Math.round(stage.clientWidth * dpr);
            canvas.height = Math.round(stage.clientHeight * dpr);
        }
        resize();
        window.addEventListener("resize", resize);

        function draw(now) {
            // Pick the figure this frame wants, falling back to the portrait
            // when the full-body image is missing or still loading.
            let figureName = FRAME_FIGURES[activeFrame] || "portrait";
            if (!loaded[figureName]) figureName = "portrait";
            const images = loaded[figureName];
            if (!images) return;
            const FIG = FIGURES[figureName];
            const base = images.base;
            const smile = images.smile;

            const cw = canvas.width;
            const ch = canvas.height;
            const iw = base.width;
            const ih = base.height;
            const frame = FIG.band;

            // Fit the frame's vertical band to the pane height, centered on
            // her face; sides letterbox on wide screens rather than zooming.
            const band = Math.max(0.1, frame.bottom - frame.top);
            const scale = ch / (band * ih);
            const drawW = iw * scale;
            const drawH = ih * scale;
            let ox = cw / 2 - FIG.face.cx * drawW;
            const oy = -frame.top * drawH;
            // Keep her horizontally on screen when the image is wider than
            // the pane (narrow/kiosk screens crop the sides symmetrically).
            if (drawW > cw) ox = Math.min(0, Math.max(cw - drawW, ox));

            ctx.setTransform(1, 0, 0, 1, 0, 0);
            ctx.fillStyle = "#0b1220";
            ctx.fillRect(0, 0, cw, ch);

            // Breathing sway: a slow rotation about her face plus a gentle
            // vertical bob. Subtle by design -- presence, not seasickness.
            const t = now / 1000;
            const swayAngle = Math.sin(t * 0.35) * 0.008 + idle.gaze.x * 0.004;
            const bob = Math.sin(t * 0.9) * drawH * 0.0016;
            const faceX = ox + FIG.face.cx * drawW;
            const faceY = oy + FIG.face.cy * drawH;
            ctx.translate(faceX, faceY + bob);
            ctx.rotate(swayAngle);
            ctx.translate(-faceX, -faceY);

            ctx.drawImage(base, ox, oy, drawW, drawH);

            // Soft-smile cross-fade, clipped to the lower face so hair
            // differences between the two shots cannot ghost.
            const smileAlpha = Math.max(0, Math.min(1, idle.smile +
                (weight("mouthSmileLeft") + weight("mouthSmileRight")) / 2));
            if (smile && FIG.smileRegion && smileAlpha > 0.02) {
                ctx.save();
                ctx.beginPath();
                ctx.rect(
                    ox + FIG.smileRegion.x0 * drawW,
                    oy + FIG.smileRegion.y0 * drawH,
                    (FIG.smileRegion.x1 - FIG.smileRegion.x0) * drawW,
                    (FIG.smileRegion.y1 - FIG.smileRegion.y0) * drawH
                );
                ctx.clip();
                ctx.globalAlpha = Math.min(0.9, smileAlpha);
                ctx.drawImage(smile, ox, oy, drawW, drawH);
                ctx.restore();
                ctx.globalAlpha = 1;
            }

            // The mouth: reveal a dark interior and translate the jaw region
            // down. Small displacements read as speech; large ones read as a
            // broken photograph, hence the conservative maxDrop.
            const open = weight("jawOpen") * (1 - weight("mouthClose") * 0.85);
            if (open > 0.02) {
                const drop = open * FIG.jaw.maxDrop * drawH;
                const mouthX = ox + FIG.mouth.x0 * drawW;
                const mouthW = (FIG.mouth.x1 - FIG.mouth.x0) * drawW;
                const lipY = oy + FIG.mouth.lip * drawH;
                ctx.fillStyle = "#2e1114";
                ctx.beginPath();
                ctx.ellipse(
                    mouthX + mouthW / 2, lipY + drop * 0.45,
                    mouthW * 0.46, Math.max(2, drop * 0.75),
                    0, 0, Math.PI * 2
                );
                ctx.fill();
                const jawSrcY = FIG.jaw.top * ih;
                const jawSrcH = (FIG.jaw.bottom - FIG.jaw.top) * ih;
                ctx.drawImage(
                    base,
                    FIG.jaw.x0 * iw, jawSrcY, (FIG.jaw.x1 - FIG.jaw.x0) * iw, jawSrcH,
                    ox + FIG.jaw.x0 * drawW, oy + FIG.jaw.top * drawH + drop,
                    (FIG.jaw.x1 - FIG.jaw.x0) * drawW, jawSrcH * scale
                );
            }

            // Blink: stretch the skin strip above each eye down over it.
            for (const eye of FIG.eyes) {
                const blink = weight(eye === FIG.eyes[0] ? "eyeBlinkLeft" : "eyeBlinkRight");
                if (blink < 0.05) continue;
                const lidSrcY = (eye.y - 0.026) * ih;
                const lidSrcH = 0.024 * ih;
                ctx.drawImage(
                    base,
                    eye.x * iw, lidSrcY, eye.w * iw, lidSrcH,
                    ox + eye.x * drawW, oy + eye.y * drawH,
                    eye.w * drawW, eye.h * drawH * blink
                );
            }

            ctx.setTransform(1, 0, 0, 1, 0, 0);
        }

        function dispose() {
            window.removeEventListener("resize", resize);
            canvas.remove();
        }

        return { ready: ready, draw: draw, dispose: dispose };
    }

    // ------------------------------------------------------------------
    // Idle BONE motion: head drift, gaze-with-lag, micro-saccades, breath.
    // This poses ARMATURE BONES per frame -- it is not a skeletal clip and
    // never becomes one (no AnimationMixer, nothing loaded). It composes
    // small rotations onto each bone's captured rest pose every frame, so
    // it can never drift or accumulate the way overwriting rotation deltas
    // would.
    //
    // The character shipped tonight (2026-08-14) has ONE armature and ONE
    // skin, and that skin IS bound to 134 joints including every neck/eye
    // bone here -- but only by the body and clothing meshes (arms, cap,
    // ankles, ...). The face meshes (char:head/mouth/eyes_inner/
    // eyes_outer/eyebrows/eyelashes) are separate, unparented, UNSKINNED
    // root nodes that the same skin never touches. So "is this bone
    // skinned to something" is true today and is the wrong question -- it
    // would rotate the collar and shoulders while the head and eyes stayed
    // frozen, which reads worse than no motion at all. The right question
    // is "is this bone skinned to the FACE", and the face is identified
    // the same name-free way the morph-target discovery code above
    // identifies it: it is whichever mesh carries the viseme/ARKit morph
    // targets (morphMeshes, passed in below). A bone only gets posed once
    // a morph-carrying mesh's skeleton actually contains it. Bone names
    // are never hardcoded (the next character, a MetaPerson export, uses
    // entirely different ones); this predicate is checked per motion
    // channel, not as one global switch -- see readiness below.
    // ------------------------------------------------------------------

    const SACCADE_INTERVAL_MIN_MS = 200;   // real fixations run ~0.2-2s
    const SACCADE_INTERVAL_MAX_MS = 2000;
    const SACCADE_AMPLITUDE_RAD = THREE.MathUtils.degToRad(2.5); // "a couple degrees"
    const GAZE_WANDER_INTERVAL_MIN_MS = 2500; // how often the base gaze point moves
    const GAZE_WANDER_INTERVAL_MAX_MS = 7000;
    const GAZE_WANDER_AMPLITUDE_RAD = THREE.MathUtils.degToRad(6);
    const EYE_EASE = 0.55;              // fast: saccades land in a couple frames (~30-50ms @60fps)
    const MAX_EYE_ROTATION_RAD = THREE.MathUtils.degToRad(12); // hard clamp, belt-and-braces
    const HEAD_FOLLOW_GAIN = 0.35;       // head moves a FRACTION of what the eyes did
    const HEAD_FOLLOW_EASE = 0.02;       // ...and arrives much later -- eyes lead, head lags
    const HEAD_DRIFT_AMPLITUDE_RAD = THREE.MathUtils.degToRad(3);
    const HEAD_DRIFT_PERIOD_A_S = 11.3;  // two incommensurate periods so drift never visibly loops
    const HEAD_DRIFT_PERIOD_B_S = 7.9;
    const MAX_NECK_ROTATION_RAD = THREE.MathUtils.degToRad(9);
    const BREATH_AMPLITUDE_RAD = THREE.MathUtils.degToRad(1.1); // spine sway, slower & smaller than head
    const BREATH_PERIOD_S = 4.3;
    const MAX_SPINE_ROTATION_RAD = THREE.MathUtils.degToRad(3);
    const SPEAKING_HEAD_DRIFT_GAIN = 1.4;    // a talking head that holds still looks dead
    const SPEAKING_GAZE_WANDER_GAIN = 0.55;  // ...but fewer big gaze wanders while talking

    // Flexible bone resolution: substring/regex match on name, case
    // insensitive, matching THREE.Bone objects only (never a Mesh that
    // happens to share the word, e.g. the char:head MESH).
    function findBones(bones, pattern) {
        return bones.filter(function (b) { return pattern.test(b.name); });
    }
    function boneSide(name) {
        const n = name.toLowerCase();
        if (n.indexOf("_l_") !== -1 || n.indexOf("left") !== -1) return "left";
        if (n.indexOf("_r_") !== -1 || n.indexOf("right") !== -1) return "right";
        return null;
    }
    // Is bone in the skeleton of a mesh that carries face morph targets?
    // Being in the skeleton of SOME skinned mesh (e.g. the body) is not
    // enough -- see the section comment above for why that measurement
    // was wrong.
    function boneReachesFace(faceSkinnedMeshes, bone) {
        return faceSkinnedMeshes.some(function (mesh) {
            return mesh.skeleton && mesh.skeleton.bones.indexOf(bone) !== -1;
        });
    }
    function boneIsSkinnedToAny(skinnedMeshes, bone) {
        return skinnedMeshes.some(function (mesh) {
            return mesh.skeleton && mesh.skeleton.bones.indexOf(bone) !== -1;
        });
    }

    // Builds the idle poser for one loaded gltfScene, or returns a no-op
    // if this rig cannot show the motion. Never throws -- a malformed or
    // unexpected rig disables the feature instead of breaking the model.
    // morphMeshes is the SAME array the caller already built by looking
    // for node.isMesh && node.morphTargetDictionary -- those meshes are,
    // by construction, the face, regardless of what anything is named.
    function createIdleBonePoser(gltfScene, morphMeshes) {
        const NOOP = { update: function () {} };
        try {
            const bones = [];
            const skinnedMeshes = [];
            gltfScene.traverse(function (node) {
                if (node.isBone) bones.push(node);
                if (node.isSkinnedMesh) skinnedMeshes.push(node);
            });
            // Of the face meshes, only the ones actually bound to a
            // skeleton can be moved by posing a bone at all.
            const faceSkinnedMeshes = morphMeshes.filter(function (m) {
                return m.isSkinnedMesh && m.skeleton;
            });

            const neckBonesAll = findBones(bones, /neck/i)
                .sort(function (a, b) { return a.name.localeCompare(b.name); });
            const eyeCandidates = findBones(bones, /eye/i);
            let leftEye = null, rightEye = null;
            for (const b of eyeCandidates) {
                const side = boneSide(b.name);
                if (side === "left" && !leftEye) leftEye = b;
                if (side === "right" && !rightEye) rightEye = b;
            }
            const spineBonesAll = findBones(bones, /spine/i)
                .sort(function (a, b) { return a.name.localeCompare(b.name); });

            // Per-channel readiness. Each motion only runs if the bone it
            // would drive is bound into a FACE mesh's skeleton -- not just
            // present in the armature, and not just skinned to the body.
            // Neck bones that don't reach the face are filtered out
            // individually rather than gating all-or-nothing on the whole
            // chain, so a rig that partially reaches the face still gets
            // partial, still-correct motion instead of none.
            const neckBones = neckBonesAll.filter(function (b) {
                return boneReachesFace(faceSkinnedMeshes, b);
            });
            const eyesReady = Boolean(leftEye && rightEye &&
                boneReachesFace(faceSkinnedMeshes, leftEye) &&
                boneReachesFace(faceSkinnedMeshes, rightEye));
            // Not "either eye" -- one eye tracking while the other stares
            // fixed would read as more broken than both staying still.

            const faceReady = neckBones.length > 0 || eyesReady;
            if (!faceReady) {
                console.info("MAE avatar: idle bone motion disabled -- " +
                    "neck/eye bones exist but are not skinned to the mesh that " +
                    "carries the face's morph targets on this rig (posing them " +
                    "would move the body, not the face).");
                return NOOP;
            }

            // Breathing sways the spine, which is squarely BODY geometry --
            // today's shipped character has that part genuinely skinned
            // (arms/cap/ankles all use these joints). But a torso that
            // breathes under a face that cannot move AT ALL is the same
            // "alive body, dead head" mismatch this whole fix exists to
            // remove, just lower down -- so breathing only runs once the
            // face itself is doing SOME motion (faceReady), rather than
            // being offered independently the moment any spine bone is
            // skinned to anything. Once faceReady, ordinary body skinning
            // is the legitimate signal for spine bones (breathing is not
            // a face channel), so any skinned mesh counts here.
            const spineBones = faceReady ? spineBonesAll.filter(function (b) {
                return boneIsSkinnedToAny(skinnedMeshes, b);
            }) : [];

            // Rest pose captured ONCE. Every frame composes an offset onto
            // this, never onto the bone's current (possibly already-posed)
            // quaternion -- that is what keeps this from drifting over a
            // long dispatch-floor session.
            const restQuat = new Map();
            const posedBones = neckBones.concat(spineBones);
            if (eyesReady) posedBones.push(leftEye, rightEye);
            for (const b of posedBones) {
                restQuat.set(b, b.quaternion.clone());
            }

            const _euler = new THREE.Euler();
            const _quat = new THREE.Quaternion();
            function poseBone(bone, pitch, yaw, roll, clamp) {
                const p = THREE.MathUtils.clamp(pitch, -clamp, clamp);
                const y = THREE.MathUtils.clamp(yaw, -clamp, clamp);
                const r = THREE.MathUtils.clamp(roll, -clamp, clamp);
                _euler.set(p, y, r, "XYZ");
                _quat.setFromEuler(_euler);
                bone.quaternion.copy(restQuat.get(bone)).multiply(_quat);
            }

            const gaze = {
                baseYaw: 0, basePitch: 0,        // slow-wandering fixation point
                nextWanderAt: 0,
                targetYaw: 0, targetPitch: 0,     // current saccade fixation
                nextSaccadeAt: 0,
                yaw: 0, pitch: 0                  // eased value actually applied
            };
            const head = { yaw: 0, pitch: 0 };

            function updateUnsafe(now) {
                const speaking = isSpeaking();
                const wanderGain = speaking ? SPEAKING_GAZE_WANDER_GAIN : 1.0;

                // Slow wandering fixation point.
                if (now >= gaze.nextWanderAt) {
                    gaze.baseYaw = (Math.random() * 2 - 1) * GAZE_WANDER_AMPLITUDE_RAD * wanderGain;
                    gaze.basePitch = (Math.random() * 2 - 1) * GAZE_WANDER_AMPLITUDE_RAD * 0.6 * wanderGain;
                    gaze.nextWanderAt = now + GAZE_WANDER_INTERVAL_MIN_MS +
                        Math.random() * (GAZE_WANDER_INTERVAL_MAX_MS - GAZE_WANDER_INTERVAL_MIN_MS);
                }
                // Micro-saccades: quick darts to a new fixation near the
                // wandering point, then hold (fixate) until the next one.
                if (now >= gaze.nextSaccadeAt) {
                    gaze.targetYaw = gaze.baseYaw + (Math.random() * 2 - 1) * SACCADE_AMPLITUDE_RAD;
                    gaze.targetPitch = gaze.basePitch + (Math.random() * 2 - 1) * SACCADE_AMPLITUDE_RAD;
                    gaze.nextSaccadeAt = now + SACCADE_INTERVAL_MIN_MS +
                        Math.random() * (SACCADE_INTERVAL_MAX_MS - SACCADE_INTERVAL_MIN_MS);
                }
                gaze.yaw += (gaze.targetYaw - gaze.yaw) * EYE_EASE;
                gaze.pitch += (gaze.targetPitch - gaze.pitch) * EYE_EASE;
                // gaze.yaw/pitch keep being computed even when eyesReady is
                // false -- head-follow below still wants a lead signal to
                // lag behind. Only the bone WRITE is gated; an unskinned
                // eye bone must never actually be posed.
                if (eyesReady) {
                    poseBone(leftEye, gaze.pitch, gaze.yaw, 0, MAX_EYE_ROTATION_RAD);
                    poseBone(rightEye, gaze.pitch, gaze.yaw, 0, MAX_EYE_ROTATION_RAD);
                }

                // Head follows the eyes, later and less -- lag comes from
                // the slower ease constant, not a time delay buffer.
                const t = now / 1000;
                const driftGain = speaking ? SPEAKING_HEAD_DRIFT_GAIN : 1.0;
                const drift = (Math.sin(t * (2 * Math.PI / HEAD_DRIFT_PERIOD_A_S)) * 0.6 +
                    Math.sin(t * (2 * Math.PI / HEAD_DRIFT_PERIOD_B_S)) * 0.4) *
                    HEAD_DRIFT_AMPLITUDE_RAD * driftGain;
                const headTargetYaw = gaze.yaw * HEAD_FOLLOW_GAIN + drift;
                const headTargetPitch = gaze.pitch * HEAD_FOLLOW_GAIN * 0.5;
                head.yaw += (headTargetYaw - head.yaw) * HEAD_FOLLOW_EASE;
                head.pitch += (headTargetPitch - head.pitch) * HEAD_FOLLOW_EASE;
                for (const b of neckBones) {
                    poseBone(b, head.pitch, head.yaw, 0, MAX_NECK_ROTATION_RAD);
                }

                // Breathing: slower, smaller, spread evenly across however
                // many spine bones are actually skinned so a longer chain
                // doesn't compound into a bigger sway than a short one.
                if (spineBones.length > 0) {
                    const perBone = BREATH_AMPLITUDE_RAD / spineBones.length;
                    const breath = Math.sin(t * (2 * Math.PI / BREATH_PERIOD_S)) * perBone;
                    for (const b of spineBones) {
                        poseBone(b, breath, 0, 0, MAX_SPINE_ROTATION_RAD);
                    }
                }
            }

            // draw() calls update() unconditionally every frame; a throw
            // here must never skip the morph/render work that follows it
            // in draw(). Disable permanently on first failure instead of
            // relying on the caller's try/catch, which would otherwise
            // silently freeze the whole avatar (not just this feature) if
            // this kept throwing frame after frame.
            let broken = false;
            function update(now) {
                if (broken) return;
                try {
                    updateUnsafe(now);
                } catch (error) {
                    broken = true;
                    console.info("MAE avatar: idle bone motion disabled after a runtime error -- " +
                        (error && error.message || error));
                }
            }

            return { update: update };
        } catch (error) {
            console.info("MAE avatar: idle bone motion disabled -- " +
                (error && error.message || error));
            return NOOP;
        }
    }

    // ------------------------------------------------------------------
    // three.js renderer: takes over when the CC5 character exists.
    // ------------------------------------------------------------------

    // Frames are DERIVED from the loaded model's bounding box, not
    // hardcoded: fixed positions written for the 1.71 m placeholder cropped
    // the top of the real 1.84 m character's head, and every re-export
    // would re-break them. Each frame names the vertical slice of the
    // character it wants (fractions of body height measured from the top);
    // the camera distance that fits that slice is computed from the fov.
    const GLTF_FRAME_SPECS = {
        // Head to mid-chest: the conversational torso view.
        console: { topMargin: 0.04, bottomFrac: 0.42, fov: 26 },
        // Full figure, hair to feet.
        booth: { topMargin: 0.03, bottomFrac: 1.03, fov: 34 }
    };

    function frameForSpec(spec, bounds) {
        const height = bounds.max.y - bounds.min.y;
        const centerX = (bounds.min.x + bounds.max.x) / 2;
        const centerZ = (bounds.min.z + bounds.max.z) / 2;
        const top = bounds.max.y + spec.topMargin * height;
        const bottom = bounds.max.y - spec.bottomFrac * height;
        const middle = (top + bottom) / 2;
        // Distance at which the vertical span exactly fills the fov, pushed
        // back from the model's front face rather than its centerline so a
        // deep asset cannot poke through the near plane.
        const span = top - bottom;
        const distance = (span / 2) / Math.tan((spec.fov * Math.PI) / 360);
        return {
            fov: spec.fov,
            position: [centerX, middle, bounds.max.z + distance],
            look: [centerX, middle, centerZ]
        };
    }

    function createGltfRenderer(gltfScene) {
        const renderer = new THREE.WebGLRenderer({ antialias: true });
        renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
        renderer.setSize(stage.clientWidth, stage.clientHeight);
        stage.appendChild(renderer.domElement);

        const scene = new THREE.Scene();
        scene.background = new THREE.Color(0x0b1220);
        scene.fog = new THREE.Fog(0x0b1220, 4, 9);
        const camera = new THREE.PerspectiveCamera(
            26, stage.clientWidth / stage.clientHeight, 0.05, 20);

        const key = new THREE.DirectionalLight(0xfff1e0, 2.4);
        key.position.set(0.7, 2.4, 1.6);
        scene.add(key);
        const fill = new THREE.DirectionalLight(0x9db8e8, 0.9);
        fill.position.set(-1.2, 1.6, 1.1);
        scene.add(fill);
        const rim = new THREE.DirectionalLight(0x2fbfae, 1.1);
        rim.position.set(0, 2.2, -1.8);
        scene.add(rim);
        scene.add(new THREE.AmbientLight(0x24304a, 1.4));
        scene.add(gltfScene);

        // Bind every mesh with morph targets; drive them by ARKit name.
        // Missing names are reported once so a bad CC5 export is loud.
        const morphMeshes = [];
        gltfScene.traverse(function (node) {
            if (node.isMesh && node.morphTargetDictionary) morphMeshes.push(node);
        });
        const found = new Set();
        for (const mesh of morphMeshes) {
            Object.keys(mesh.morphTargetDictionary).forEach(function (name) {
                found.add(name);
            });
        }
        // Self-disables to a zero-cost no-op unless the loaded rig's face
        // meshes are actually skinned to the bones it would pose -- see the
        // "Idle BONE motion" section above for why that check exists.
        const bonePoser = createIdleBonePoser(gltfScene, morphMeshes);
        console.info(
            "MAE avatar: model exposes " + found.size + " morph targets:",
            Array.from(found).sort().join(", "));
        // Two rig contracts are accepted: ARKit names (Character Creator
        // pipeline) or native Polly viseme morphs (Sumerian Host bake).
        // Warn against whichever contract the model is closest to honouring.
        const isNativeViseme = found.has("viseme_sil") || found.has("viseme_p");
        if (isNativeViseme) {
            for (const name of NATIVE_VISEME_KEYS.concat(["blink"])) {
                if (!found.has(name)) {
                    console.warn("MAE avatar: viseme model is missing morph '" +
                        name + "' -- that Polly viseme will not move her mouth.");
                }
            }
        } else {
            for (const name of MOUTH_KEYS.concat(["eyeBlinkLeft", "eyeBlinkRight"])) {
                if (!found.has(name)) {
                    console.warn("MAE avatar: model is missing ARKit shape '" + name +
                        "' -- was the export made with the ARKit profile on?");
                }
            }
        }

        const bounds = new THREE.Box3().setFromObject(gltfScene);
        const frames = {
            console: frameForSpec(GLTF_FRAME_SPECS.console, bounds),
            booth: frameForSpec(GLTF_FRAME_SPECS.booth, bounds)
        };
        // Read-only debug surface: lets a console (or an agent driving one)
        // verify what the derived frames actually contain without guessing
        // at closure state. projectY answers "where on screen is this world
        // height" in [0,1] from the top of the pane.
        window.LCDashAvatarDebug = {
            bounds: { min: bounds.min.toArray(), max: bounds.max.toArray() },
            frames: frames,
            weights: weights,
            // Ground truth: what the mesh ACTUALLY has applied this frame,
            // not what the state object claims -- catches a dictionary
            // name mismatch that would silently no-op the whole pipeline.
            liveInfluence: function (morphName) {
                for (const mesh of morphMeshes) {
                    const idx = mesh.morphTargetDictionary[morphName];
                    if (idx !== undefined) {
                        return { mesh: mesh.name, influence: mesh.morphTargetInfluences[idx] };
                    }
                }
                return null;
            },
            // Runs exactly the weights->mesh copy step draw() does, without
            // needing a visible/rendering tab to pump requestAnimationFrame.
            applyWeightsNow: function () {
                for (const mesh of morphMeshes) {
                    const dict = mesh.morphTargetDictionary;
                    for (const name of Object.keys(dict)) {
                        if (name in weights) {
                            mesh.morphTargetInfluences[dict[name]] = weights[name];
                        }
                    }
                }
            },
            projectY: function (worldY, frameName) {
                const frame = frames[frameName] || frames.console;
                const probe = new THREE.PerspectiveCamera(
                    frame.fov, camera.aspect, 0.05, 20);
                probe.position.set(...frame.position);
                probe.lookAt(...frame.look);
                probe.updateProjectionMatrix();
                const point = new THREE.Vector3(
                    frame.look[0], worldY, frame.look[2]);
                point.project(probe);
                return (1 - point.y) / 2;
            }
        };

        function applyFrame() {
            const frame = frames[activeFrame] || frames.console;
            camera.fov = frame.fov;
            camera.position.set(...frame.position);
            camera.lookAt(...frame.look);
            camera.updateProjectionMatrix();
        }
        applyFrame();

        function resize() {
            camera.aspect = stage.clientWidth / stage.clientHeight;
            camera.updateProjectionMatrix();
            renderer.setSize(stage.clientWidth, stage.clientHeight);
        }
        window.addEventListener("resize", resize);

        function draw(now) {
            applyFrame();
            const t = now / 1000;
            gltfScene.rotation.y = Math.sin(t * 0.11) * 0.02;
            bonePoser.update(now);
            for (const mesh of morphMeshes) {
                const dict = mesh.morphTargetDictionary;
                for (const name of Object.keys(dict)) {
                    if (name in weights) {
                        mesh.morphTargetInfluences[dict[name]] = weights[name];
                    }
                }
            }
            renderer.render(scene, camera);
        }

        return { draw: draw };
    }

    // ------------------------------------------------------------------
    // Boot the renderers and the shared animation loop.
    // ------------------------------------------------------------------

    let active = createPortraitRenderer();
    markActiveFramePill();

    // The character takes over the moment mae.glb exists; a 404 keeps the
    // portraits. The converter Draco-compresses the meshes (that is most of
    // the difference between an 80 MB and a 29 MB file), and GLTFLoader
    // REFUSES Draco content unless a decoder is attached -- the first ship
    // of the real model fell back to portraits exactly this way, with a log
    // line that wrongly claimed the file didn't exist.
    const dracoLoader = new DRACOLoader();
    dracoLoader.setDecoderPath("/static/vendor/three-0.171.0/draco/");
    const gltfLoader = new GLTFLoader();
    gltfLoader.setDRACOLoader(dracoLoader);
    gltfLoader.load(
        "/static/models/mae.glb",
        function (gltf) {
            try {
                const gltfRenderer = createGltfRenderer(gltf.scene);
                active.dispose();
                active = gltfRenderer;
                addStatusLine("Character model loaded.");
            } catch (error) {
                console.warn("MAE avatar: model renderer failed, keeping portraits.", error);
            }
        },
        undefined,
        function (error) {
            // A missing file and a file the loader cannot parse are very
            // different problems; say which one actually happened.
            console.warn(
                "MAE avatar: mae.glb did not load; portrait mode active.",
                error && (error.message || error));
        });

    // A thrown frame must never end the animation: requestAnimationFrame
    // only continues if it is called again, so an exception anywhere in
    // draw() used to freeze MAE permanently on her last painted frame --
    // silently, since the canvas keeps showing it. The rescheduling now
    // happens in `finally`, and the first failure is reported once rather
    // than on every frame at 60 Hz.
    let frameErrorReported = false;

    function animationLoop(now) {
        try {
            idleStep(now);
            const targets = activeVisemeTargets();
            // Loudness scales mouth-OPENING shapes only; falls back to 1.0
            // (today's behaviour) on any Web Audio failure -- see
            // currentAmplitudeGain(), which never throws.
            const amplitudeGain = currentAmplitudeGain();
            // Attack faster than release so consonant closures register.
            // Every driven key decays to zero unless the active viseme
            // names it -- including the native viseme_* morphs, or a
            // Sumerian-baked mouth would hold its last shape forever.
            for (const key of DRIVEN_MOUTH_KEYS) {
                let target = targets[key] || 0;
                if (target > 0 && !AMPLITUDE_EXEMPT_KEYS.has(key)) {
                    target *= amplitudeGain;
                }
                const current = weight(key);
                const alpha = target > current ? 0.45 : 0.28;
                setWeight(key, current + (target - current) * alpha);
            }
            active.draw(now);
        } catch (error) {
            if (!frameErrorReported) {
                frameErrorReported = true;
                console.error("MAE avatar: frame failed; animation continues.", error);
            }
        } finally {
            window.requestAnimationFrame(animationLoop);
        }
    }
    window.requestAnimationFrame(animationLoop);

    // Portrait aspect (the 512x1536 booth panel) defaults to the full
    // portrait framing.
    activeFrame = window.innerWidth / window.innerHeight < 0.55 ? "booth" : "console";
    markActiveFramePill();

    // ------------------------------------------------------------------
    // Speech: text -> /api/mae/avatar/speech -> audio + viseme timeline.
    // ------------------------------------------------------------------

    const SPEECH_MAX_CHARS = 2400;

    function splitForSpeech(text) {
        const clean = String(text || "")
            .replace(/\s+/g, " ")
            .trim();
        if (!clean) return [];
        const sentences = clean.match(/[^.!?]+[.!?]*\s*/g) || [clean];
        const chunks = [];
        let current = "";
        for (let sentence of sentences) {
            if ((current + sentence).length > SPEECH_MAX_CHARS && current) {
                chunks.push(current.trim());
                current = "";
            }
            while (sentence.length > SPEECH_MAX_CHARS) {
                chunks.push(sentence.slice(0, SPEECH_MAX_CHARS));
                sentence = sentence.slice(SPEECH_MAX_CHARS);
            }
            current += sentence;
        }
        if (current.trim()) chunks.push(current.trim());
        return chunks;
    }

    // Set when the browser refuses playback for want of a user gesture, so
    // the opening greeting can be spoken on the first click instead of
    // being lost silently.
    let autoplayBlocked = false;

    const speech = (function () {
        let sessionId = 0;
        let queue = Promise.resolve();
        let speaking = 0;

        function begin() {
            sessionId += 1;
            queue = Promise.resolve();
            stopPlayback();
            return sessionId;
        }

        function stopPlayback() {
            if (speechState.audio) {
                try { speechState.audio.pause(); } catch (error) { /* stopped */ }
                if (speechState.audio.dataset.objectUrl) {
                    URL.revokeObjectURL(speechState.audio.dataset.objectUrl);
                }
            }
            speechState.audio = null;
            speechState.visemes = [];
            speechState.cursor = 0;
            speaking = 0;
            stopButton.style.display = "none";
        }

        async function synthesize(text) {
            const response = await fetch("/api/mae/avatar/speech", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                cache: "no-store",
                body: JSON.stringify({ text: text })
            });
            if (!response.ok) {
                const detail = await response.json().catch(function () { return {}; });
                throw new Error(detail.detail || "MAE could not generate speech.");
            }
            return response.json();
        }

        function play(id, payload) {
            return new Promise(function (resolve) {
                if (id !== sessionId) { resolve(); return; }
                const bytes = Uint8Array.from(
                    atob(payload.audio_base64), function (c) { return c.charCodeAt(0); });
                const url = URL.createObjectURL(new Blob([bytes], { type: "audio/mpeg" }));
                const audio = new Audio(url);
                audio.dataset.objectUrl = url;
                speechState.audio = audio;
                speechState.visemes = (payload.visemes || []).slice()
                    .sort(function (a, b) { return a.time_ms - b.time_ms; });
                speechState.cursor = 0;
                speaking += 1;
                stopButton.style.display = "";
                // Resume first, THEN attach: attachAmplitudeAnalyser
                // refuses to capture the element while the context is
                // suspended, so asking for the resume first is what lets
                // amplitude scaling switch on at all. resume() is async, so
                // the first utterance typically still plays unanalysed and
                // later ones pick it up -- sound always wins over gain.
                resumeAudioCtxOnGesture();
                // Amplitude analysis is best-effort: attachAmplitudeAnalyser
                // already swallows its own failures and returns null, so a
                // failure here never blocks playback below.
                attachAmplitudeAnalyser(audio);
                function finish() {
                    URL.revokeObjectURL(url);
                    if (speechState.audio === audio) {
                        speechState.audio = null;
                        speechState.visemes = [];
                        speechState.cursor = 0;
                    }
                    speaking = Math.max(0, speaking - 1);
                    if (!speaking && id === sessionId) stopButton.style.display = "none";
                    resolve();
                }
                audio.addEventListener("ended", finish, { once: true });
                audio.addEventListener("error", finish, { once: true });
                audio.play().then(function () {
                    autoplayBlocked = false;
                }).catch(function (error) {
                    // NotAllowedError means the browser refused autoplay
                    // before any user gesture -- recoverable on the next
                    // click, unlike a decode or network failure.
                    if (error && error.name === "NotAllowedError") {
                        autoplayBlocked = true;
                    }
                    finish();
                });
            });
        }

        function enqueue(id, text) {
            const spoken = String(text || "").trim();
            if (!spoken || id !== sessionId) return;
            // Decided per chunk, at speak time: if Rapport's session is
            // connected AND it accepts this chunk, it is now the one saying
            // these words through Polly Ruth. Playing our own synthesis on
            // top would speak the reply twice, and driving speechState's
            // visemes would move the idle local mouth for words coming out
            // of a different face -- so skip local synthesis+playback
            // entirely and leave the local renderer idling. If Rapport is
            // not connected (never started, still negotiating, or dropped
            // mid-conversation), rapport.speak() returns false and this
            // chunk falls straight through to the local path below, same
            // as if the flag were off.
            if (rapport.isActive() && rapport.speak(spoken)) return;
            // Synthesis for the next chunk overlaps playback of the current
            // one; a failed chunk is skipped without breaking the chain.
            const synthesis = synthesize(spoken).catch(function (error) {
                console.warn("MAE avatar speech failed:", error.message);
                return null;
            });
            queue = queue.then(function () {
                if (id !== sessionId) return null;
                return synthesis.then(function (payload) {
                    return payload ? play(id, payload) : null;
                });
            });
        }

        function idle() { return queue; }
        function stop() { sessionId += 1; queue = Promise.resolve(); stopPlayback(); }

        return { begin: begin, enqueue: enqueue, idle: idle, stop: stop };
    })();

    stopButton.addEventListener("click", function () {
        speech.stop();
        rapport.stop();
    });

    // ------------------------------------------------------------------
    // Transcript UI
    // ------------------------------------------------------------------

    function addMessage(kind, text) {
        const row = document.createElement("div");
        row.className = "msg " + kind;
        const bubble = document.createElement("div");
        bubble.textContent = text;
        row.appendChild(bubble);
        transcript.appendChild(row);
        while (transcript.children.length > 60) {
            transcript.removeChild(transcript.firstChild);
        }
        transcript.scrollTop = transcript.scrollHeight;
        return bubble;
    }
    function addStatusLine(text) { addMessage("status", text); }

    // ------------------------------------------------------------------
    // Conversation: stream first for sentence-one latency, whole-answer
    // advisory as fallback. Same endpoints as the MAE page.
    // ------------------------------------------------------------------

    let busy = false;
    let advisoryStreamAvailable = true;

    async function readNdjsonStream(response, consumeEvent) {
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        for (;;) {
            const part = await reader.read();
            buffer += decoder.decode(part.value || new Uint8Array(), { stream: !part.done });
            const lines = buffer.split("\n");
            buffer = lines.pop() || "";
            lines.filter(Boolean).forEach(function (line) {
                consumeEvent(JSON.parse(line));
            });
            if (part.done) break;
        }
        if (buffer.trim()) consumeEvent(JSON.parse(buffer));
    }

    async function askStreaming(question, sessionId) {
        const response = await fetch("/api/cloud-ai/advisory/stream", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            cache: "no-store",
            body: JSON.stringify({ question: question, persona: "mae" })
        });
        if (response.status === 404 || response.status === 405) {
            advisoryStreamAvailable = false;
            throw new Error("stream-unavailable");
        }
        if (!response.ok || !response.body) throw new Error("stream-unavailable");

        let payload = null;
        let failure = "";
        let spokeChunks = false;
        function consumeEvent(event) {
            if (!event || typeof event !== "object") return;
            if (event.type === "chunk") {
                const chunkText = String(event.speech || event.text || "");
                if (chunkText.trim()) {
                    spokeChunks = true;
                    speech.enqueue(sessionId, chunkText);
                }
            } else if (event.type === "complete") {
                payload = event.payload || {};
            } else if (event.type === "error") {
                failure = String(event.detail || "");
            }
        }
        await readNdjsonStream(response, consumeEvent);
        if (failure) throw new Error(failure);
        if (!payload) throw new Error("The advisory stream ended early.");
        return { payload: payload, spokeChunks: spokeChunks };
    }

    async function askWhole(question) {
        const response = await fetch("/api/cloud-ai/advisory", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            cache: "no-store",
            body: JSON.stringify({ question: question, persona: "mae" })
        });
        const payload = await response.json().catch(function () { return {}; });
        if (!response.ok) {
            throw new Error(payload.detail || "MAE could not answer that.");
        }
        return payload;
    }

    async function ask(question) {
        const trimmed = String(question || "").trim();
        if (!trimmed || busy) return;
        busy = true;
        sendButton.disabled = true;
        addMessage("user", trimmed);
        const sessionId = speech.begin();
        try {
            let payload = null;
            let spokeChunks = false;
            if (cloudMode && advisoryStreamAvailable) {
                try {
                    const streamed = await askStreaming(trimmed, sessionId);
                    payload = streamed.payload;
                    spokeChunks = streamed.spokeChunks;
                } catch (error) {
                    if (error.message !== "stream-unavailable") throw error;
                }
            }
            if (!payload) payload = await askWhole(trimmed);
            const answer = payload.denied
                ? (payload.denial_reason || "I can't help with that request.")
                : String(payload.answer || "").trim();
            addMessage("mae", answer || "I don't have an answer for that right now.");
            if (!spokeChunks && answer) {
                splitForSpeech(answer).forEach(function (chunk) {
                    speech.enqueue(sessionId, chunk);
                });
            }
            await speech.idle();
        } catch (error) {
            addStatusLine(error.message || "MAE could not answer that.");
        } finally {
            busy = false;
            sendButton.disabled = false;
        }
    }

    sendButton.addEventListener("click", function () {
        const value = questionInput.value;
        questionInput.value = "";
        ask(value);
    });
    questionInput.addEventListener("keydown", function (event) {
        if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            sendButton.click();
        }
    });

    // ------------------------------------------------------------------
    // Push-to-talk: hold the button, release to transcribe and ask.
    // ------------------------------------------------------------------

    // Cloud STT is Amazon Transcribe streaming, which accepts only pcm and
    // ogg-opus -- the MediaRecorder default (audio/webm;codecs=opus) is
    // refused with transcribe_format_not_allowed, which is exactly what this
    // page shipped with. Cloud mode therefore captures raw 16 kHz PCM via
    // LCDashVoiceCapture (the same module the MAE page uses); MediaRecorder
    // remains for on-prem, whose faster-whisper path wants a container.
    let pcmCapture = null;
    let pcmAudioContext = null;
    let pcmSourceNode = null;
    let pcmStream = null;
    let recorder = null;
    let recorderChunks = [];
    let recorderStream = null;

    function usePcmCapture() {
        return cloudMode && Boolean(window.LCDashVoiceCapture);
    }

    // Release the microphone and audio graph. Leaving the stream open holds
    // the browser's recording indicator on and keeps the mic hot between
    // questions, which is not something a 911 console should ever do.
    function releasePcmGraph() {
        pcmCapture = null;
        pcmSourceNode = null;
        if (pcmAudioContext) {
            const context = pcmAudioContext;
            pcmAudioContext = null;
            context.close().catch(function () { /* already closed */ });
        }
        if (pcmStream) {
            pcmStream.getTracks().forEach(function (track) { track.stop(); });
            pcmStream = null;
        }
    }

    function setRecordingUi(active) {
        pttButton.classList.toggle("recording", active);
        if (active) {
            pttButton.textContent = "Listening… release to send";
        } else {
            pttButton.innerHTML = "&#127908; Hold to talk";
        }
    }

    async function startRecording() {
        if (pcmCapture || recorder || busy) return;
        speech.stop();

        if (usePcmCapture()) {
            try {
                // The capture module records from an existing graph rather
                // than opening the microphone itself, so the stream, context,
                // and source node are ours to create and to tear down.
                pcmStream = await navigator.mediaDevices.getUserMedia({ audio: true });
                const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
                pcmAudioContext = new AudioContextCtor();
                if (pcmAudioContext.state === "suspended") {
                    await pcmAudioContext.resume();
                }
                pcmSourceNode = pcmAudioContext.createMediaStreamSource(pcmStream);
                pcmCapture = window.LCDashVoiceCapture.start(pcmAudioContext, pcmSourceNode);
            } catch (error) {
                releasePcmGraph();
                addStatusLine(
                    "Microphone unavailable: " + (error.message || "permission denied"));
                return;
            }
            setRecordingUi(true);
            return;
        }

        try {
            recorderStream = await navigator.mediaDevices.getUserMedia({ audio: true });
        } catch (error) {
            addStatusLine("Microphone unavailable: " + (error.message || "permission denied"));
            return;
        }
        recorderChunks = [];
        try {
            recorder = new MediaRecorder(recorderStream, { mimeType: "audio/webm;codecs=opus" });
        } catch (error) {
            recorder = new MediaRecorder(recorderStream);
        }
        recorder.addEventListener("dataavailable", function (event) {
            if (event.data && event.data.size) recorderChunks.push(event.data);
        });
        recorder.addEventListener("stop", function () {
            const blob = new Blob(recorderChunks, { type: "audio/webm" });
            recorder = null;
            recorderChunks = [];
            if (recorderStream) {
                recorderStream.getTracks().forEach(function (track) { track.stop(); });
                recorderStream = null;
            }
            submitRecording(blob, null);
        });
        recorder.start();
        setRecordingUi(true);
    }

    async function submitRecording(blob, clip) {
        setRecordingUi(false);
        if (!blob || blob.size < 2000) return;
        addStatusLine("Understanding your question…");
        try {
            const formData = new FormData();
            if (clip && clip.audioFormat === "pcm") {
                // The endpoint defaults to webm-opus/48000; raw PCM must
                // declare its own format and rate or the push-to-talk
                // contract rejects it.
                formData.append("file", blob, "mae-question.pcm");
                formData.append("audio_format", clip.audioFormat);
                formData.append("sample_rate_hz", String(clip.sampleRateHz));
                formData.append(
                    "duration_seconds",
                    String(Math.min(30, Math.max(0.1, clip.durationSeconds)))
                );
            } else {
                formData.append("file", blob, "mae-question.webm");
            }
            const response = await fetch("/api/voice/transcribe", {
                method: "POST",
                cache: "no-store",
                body: formData
            });
            const payload = await response.json();
            if (!response.ok) {
                throw new Error(payload.detail || "MAE could not transcribe the question.");
            }
            const question = String(payload.text || "").trim();
            if (question.length < 2) {
                addStatusLine("I did not catch that — please try again.");
                return;
            }
            await ask(question);
        } catch (error) {
            addStatusLine(error.message || "Voice request failed.");
        }
    }

    function stopRecording() {
        if (pcmCapture) {
            const capture = pcmCapture;
            pcmCapture = null;
            capture.stop().then(function (clip) {
                releasePcmGraph();
                submitRecording(clip ? clip.blob : null, clip);
            }).catch(function () {
                releasePcmGraph();
                submitRecording(null, null);
            });
            return;
        }
        if (recorder && recorder.state === "recording") recorder.stop();
    }

    pttButton.addEventListener("pointerdown", function (event) {
        event.preventDefault();
        startRecording();
    });
    pttButton.addEventListener("pointerup", stopRecording);
    pttButton.addEventListener("pointerleave", stopRecording);
    pttButton.addEventListener("pointercancel", stopRecording);

    // ------------------------------------------------------------------
    // Framing controls and status pills
    // ------------------------------------------------------------------

    document.querySelectorAll("[data-frame]").forEach(function (button) {
        button.addEventListener("click", function () {
            activeFrame = button.dataset.frame === "booth" ? "booth" : "console";
            markActiveFramePill();
        });
    });

    async function loadIdentity() {
        try {
            const response = await fetch("/api/identity/whoami", { cache: "no-store" });
            const payload = await response.json();
            identityPill.textContent = payload.verified
                ? (payload.name + " · " + payload.role_label)
                : "Not signed in";
            identityPill.classList.toggle("ok", Boolean(payload.verified));
        } catch (error) {
            identityPill.textContent = "Identity unavailable";
        }
    }

    async function loadVoiceStatus() {
        if (!cloudMode) {
            voicePill.textContent = "On-prem voice";
            return;
        }
        try {
            const response = await fetch("/api/cloud-ai/status", { cache: "no-store" });
            const payload = await response.json();
            const tts = payload.tts || {};
            const stt = payload.stt || {};
            if (tts.ready) {
                voicePill.textContent = "Voice ready · " + (tts.voice || "neural");
                voicePill.classList.add("ok");
                voicePill.classList.remove("err");
            } else {
                voicePill.textContent = tts.disabled_reason || "Voice unavailable";
                voicePill.classList.add("err");
            }
            pttButton.disabled = !stt.ready;
            if (!stt.ready) pttButton.title = stt.disabled_reason || "Push-to-talk unavailable";
        } catch (error) {
            voicePill.textContent = "Voice status unavailable";
            voicePill.classList.add("err");
        }
    }

    // ------------------------------------------------------------------
    // Boot
    // ------------------------------------------------------------------

    // MAE greets whoever opened her, by name when identity is verified, and
    // says it aloud. Composed here rather than asked of the model: a
    // greeting must be instant and identical every time, and the advisory
    // path costs a Bedrock round trip to produce a fixed sentence. Speech
    // failure is silent -- the written greeting still lands, and a muted
    // MAE beats an error toast as a first impression.
    let greetingText = "";
    let greetingSpoken = false;

    async function greet() {
        let name = "";
        try {
            const response = await fetch("/api/identity/whoami", { cache: "no-store" });
            const payload = await response.json();
            if (payload.verified) {
                // The badge carries an email; the local part is close enough
                // to a name for a greeting and never exposes the domain.
                name = String(payload.name || "").split("@")[0].replace(/[._-]+/g, " ").trim();
            }
        } catch (error) {
            name = "";
        }
        const hour = new Date().getHours();
        const partOfDay = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
        const greeting = name
            ? partOfDay + ", " + name + ". I'm MAE."
            : partOfDay + ". I'm MAE, the Mission Assistance Engine.";
        const offer = " Ask me about active calls, wait times, or unit coverage.";

        greetingText = greeting + offer;
        addMessage("mae", greetingText);
        speakGreeting();
    }

    function speakGreeting() {
        if (!greetingText || greetingSpoken) return;
        const sessionId = speech.begin();
        speech.enqueue(sessionId, greetingText);
        // Only count it as delivered if playback was not refused for want
        // of a user gesture; otherwise leave it pending for the first click.
        speech.idle().then(function () {
            if (!autoplayBlocked) greetingSpoken = true;
        });
    }

    loadIdentity();
    loadVoiceStatus();
    window.setInterval(loadVoiceStatus, 60000);
    greet();
    // Browsers block autoplay until the page has been interacted with, and
    // a blocked play() rejects silently. If the greeting audio never
    // actually started, the first click or keypress speaks it -- keyed on
    // whether it PLAYED, not on whether it was attempted.
    ["pointerdown", "keydown"].forEach(function (eventName) {
        window.addEventListener(eventName, function () {
            if (autoplayBlocked && !greetingSpoken) {
                autoplayBlocked = false;
                speakGreeting();
            }
        }, { once: true });
    });
})();
