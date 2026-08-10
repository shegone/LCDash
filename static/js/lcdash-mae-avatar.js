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

    function activeVisemeTargets() {
        if (!isSpeaking()) return VISEME_TARGETS.sil;
        // 60 ms lookahead approximates co-articulation: the mouth starts
        // forming a shape slightly before the sound lands.
        const now = speechState.audio.currentTime * 1000 + 60;
        const marks = speechState.visemes;
        while (
            speechState.cursor + 1 < marks.length &&
            marks[speechState.cursor + 1].time_ms <= now
        ) {
            speechState.cursor += 1;
        }
        const mark = marks[speechState.cursor];
        if (!mark || mark.time_ms > now) return VISEME_TARGETS.sil;
        return VISEME_TARGETS[mark.viseme] || VISEME_TARGETS.sil;
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

    // Landmarks measured on the 832x1248 reference set (all portraits share
    // one framing), normalized so any same-framing re-export keeps working.
    const FACE = { cx: 0.469, cy: 0.399 };
    const MOUTH = { x0: 0.397, x1: 0.575, lip: 0.561 };
    const JAW = { x0: 0.33, x1: 0.66, top: 0.558, bottom: 0.665, maxDrop: 0.019 };
    const EYES = [
        { x: 0.300, y: 0.377, w: 0.097, h: 0.046 },
        { x: 0.547, y: 0.377, w: 0.097, h: 0.046 }
    ];
    const SMILE_REGION = { x0: 0.28, x1: 0.70, y0: 0.43, y1: 0.68 };

    // Framing presets: which vertical band of the portrait fills the pane's
    // height. Console is head-and-shoulders; booth is the whole portrait.
    // The sides letterbox against the page background on wide screens --
    // multiplying a zoom onto cover-fit instead meant a widescreen monitor
    // showed only her eyes.
    const PORTRAIT_FRAMES = {
        console: { top: 0.13, bottom: 0.86 },
        booth: { top: 0.0, bottom: 1.0 }
    };
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

        let base = null;
        let smile = null;

        function loadImage(src) {
            return new Promise(function (resolve, reject) {
                const img = new Image();
                img.onload = function () { resolve(img); };
                img.onerror = reject;
                img.src = src;
            });
        }

        const ready = Promise.all([
            loadImage("/static/img/mae/mae-neutral.jpg"),
            loadImage("/static/img/mae/mae-soft-smile.jpg")
        ]).then(function (images) {
            base = images[0];
            smile = images[1];
            // First paint immediately: rAF is throttled or paused in hidden
            // or backgrounded panes, and she should be there the moment the
            // page becomes visible rather than one frame later.
            draw(performance.now());
        }).catch(function () {
            // Static portrait fallback: still MAE, just not animated.
            canvas.remove();
            fallback.style.display = "flex";
        });

        function resize() {
            const dpr = Math.min(window.devicePixelRatio || 1, 2);
            canvas.width = Math.round(stage.clientWidth * dpr);
            canvas.height = Math.round(stage.clientHeight * dpr);
        }
        resize();
        window.addEventListener("resize", resize);

        function draw(now) {
            if (!base) return;
            const cw = canvas.width;
            const ch = canvas.height;
            const iw = base.width;
            const ih = base.height;
            const frame = PORTRAIT_FRAMES[activeFrame] || PORTRAIT_FRAMES.console;

            // Fit the frame's vertical band to the pane height, centered on
            // her face; sides letterbox on wide screens rather than zooming.
            const band = Math.max(0.1, frame.bottom - frame.top);
            const scale = ch / (band * ih);
            const drawW = iw * scale;
            const drawH = ih * scale;
            let ox = cw / 2 - FACE.cx * drawW;
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
            const faceX = ox + FACE.cx * drawW;
            const faceY = oy + FACE.cy * drawH;
            ctx.translate(faceX, faceY + bob);
            ctx.rotate(swayAngle);
            ctx.translate(-faceX, -faceY);

            ctx.drawImage(base, ox, oy, drawW, drawH);

            // Soft-smile cross-fade, clipped to the lower face so hair
            // differences between the two shots cannot ghost.
            const smileAlpha = Math.max(0, Math.min(1, idle.smile +
                (weight("mouthSmileLeft") + weight("mouthSmileRight")) / 2));
            if (smile && smileAlpha > 0.02) {
                ctx.save();
                ctx.beginPath();
                ctx.rect(
                    ox + SMILE_REGION.x0 * drawW,
                    oy + SMILE_REGION.y0 * drawH,
                    (SMILE_REGION.x1 - SMILE_REGION.x0) * drawW,
                    (SMILE_REGION.y1 - SMILE_REGION.y0) * drawH
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
                const drop = open * JAW.maxDrop * drawH;
                const mouthX = ox + MOUTH.x0 * drawW;
                const mouthW = (MOUTH.x1 - MOUTH.x0) * drawW;
                const lipY = oy + MOUTH.lip * drawH;
                ctx.fillStyle = "#2e1114";
                ctx.beginPath();
                ctx.ellipse(
                    mouthX + mouthW / 2, lipY + drop * 0.45,
                    mouthW * 0.46, Math.max(2, drop * 0.75),
                    0, 0, Math.PI * 2
                );
                ctx.fill();
                const jawSrcY = JAW.top * ih;
                const jawSrcH = (JAW.bottom - JAW.top) * ih;
                ctx.drawImage(
                    base,
                    JAW.x0 * iw, jawSrcY, (JAW.x1 - JAW.x0) * iw, jawSrcH,
                    ox + JAW.x0 * drawW, oy + JAW.top * drawH + drop,
                    (JAW.x1 - JAW.x0) * drawW, jawSrcH * scale
                );
            }

            // Blink: stretch the skin strip above each eye down over it.
            for (const eye of EYES) {
                const blink = weight(eye === EYES[0] ? "eyeBlinkLeft" : "eyeBlinkRight");
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
    // three.js renderer: takes over when the CC5 character exists.
    // ------------------------------------------------------------------

    const GLTF_FRAMES = {
        console: { position: [0, 1.615, 0.52], look: [0, 1.6, 0], fov: 26 },
        booth: { position: [0, 1.25, 2.7], look: [0, 1.05, 0], fov: 34 }
    };

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
        console.info(
            "MAE avatar: model exposes " + found.size + " morph targets:",
            Array.from(found).sort().join(", "));
        for (const name of MOUTH_KEYS.concat(["eyeBlinkLeft", "eyeBlinkRight"])) {
            if (!found.has(name)) {
                console.warn("MAE avatar: model is missing ARKit shape '" + name +
                    "' -- was the export made with the ARKit profile on?");
            }
        }

        function applyFrame() {
            const frame = GLTF_FRAMES[activeFrame] || GLTF_FRAMES.console;
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

    // The CC5 character takes over the moment it exists; the 404 until
    // Phase 0 delivers is expected and the portraits stay.
    new GLTFLoader().load(
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
        function () {
            console.info("MAE avatar: no /static/models/mae.glb yet; portrait mode active.");
        });

    function animationLoop(now) {
        idleStep(now);
        const targets = activeVisemeTargets();
        // Attack faster than release so consonant closures register.
        for (const key of MOUTH_KEYS) {
            const target = targets[key] || 0;
            const current = weight(key);
            const alpha = target > current ? 0.45 : 0.28;
            setWeight(key, current + (target - current) * alpha);
        }
        active.draw(now);
        window.requestAnimationFrame(animationLoop);
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
                audio.play().catch(finish);
            });
        }

        function enqueue(id, text) {
            const spoken = String(text || "").trim();
            if (!spoken || id !== sessionId) return;
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

    stopButton.addEventListener("click", function () { speech.stop(); });

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

    let recorder = null;
    let recorderChunks = [];
    let recorderStream = null;

    async function startRecording() {
        if (recorder || busy) return;
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
        recorder.addEventListener("stop", onRecordingStopped);
        recorder.start();
        pttButton.classList.add("recording");
        pttButton.textContent = "Listening… release to send";
        speech.stop();
    }

    async function onRecordingStopped() {
        pttButton.classList.remove("recording");
        pttButton.innerHTML = "&#127908; Hold to talk";
        const blob = new Blob(recorderChunks, { type: "audio/webm" });
        recorder = null;
        recorderChunks = [];
        if (recorderStream) {
            recorderStream.getTracks().forEach(function (track) { track.stop(); });
            recorderStream = null;
        }
        if (blob.size < 2000) return;
        addStatusLine("Understanding your question…");
        try {
            const formData = new FormData();
            formData.append("file", blob, "mae-question.webm");
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

    loadIdentity();
    loadVoiceStatus();
    window.setInterval(loadVoiceStatus, 60000);
    addStatusLine("MAE is ready. Ask about active calls, wait times, or coverage.");
})();
