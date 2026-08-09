// MAE avatar runtime -- Phase 1 of docs/planning/MAE_AVATAR_PLAN_2026-08-09.md.
//
// A client-rendered figure driven by ARKit-style blendshape weights. The
// weight *names* are the contract: today a procedural placeholder interprets
// a small subset and Polly visemes drive the mouth; when the Character
// Creator 5 export lands at /static/models/mae.glb the same weights drive
// its real morph targets, and when Audio2Face streams weights later, only
// the source changes. Rendering is always local -- no server GPU.

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

    function activeVisemeTargets() {
        const audio = speechState.audio;
        if (!audio || audio.paused || audio.ended) return VISEME_TARGETS.sil;
        // 60 ms lookahead approximates co-articulation: the mouth starts
        // forming a shape slightly before the sound lands.
        const now = audio.currentTime * 1000 + 60;
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
        nextGazeAt: 0
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

        // A touch of engagement while speaking.
        const speaking = speechState.audio && !speechState.audio.paused && !speechState.audio.ended;
        setWeight("browInnerUp", speaking ? 0.18 : 0.06);
    }

    // ------------------------------------------------------------------
    // Renderer, scene, and the placeholder figure.
    // ------------------------------------------------------------------

    let renderer = null;
    let scene = null;
    let camera = null;
    let rig = null;

    const FRAMES = {
        console: { position: [0, 1.615, 0.52], look: [0, 1.6, 0], fov: 26 },
        booth: { position: [0, 1.25, 2.7], look: [0, 1.05, 0], fov: 34 }
    };
    let activeFrame = "console";

    function applyFrame(name) {
        if (!camera) return;
        const frame = FRAMES[name] || FRAMES.console;
        activeFrame = FRAMES[name] ? name : "console";
        camera.fov = frame.fov;
        camera.position.set(...frame.position);
        camera.lookAt(...frame.look);
        camera.updateProjectionMatrix();
        document.getElementById("pill-frame-console").classList.toggle(
            "frame-active", activeFrame === "console");
        document.getElementById("pill-frame-booth").classList.toggle(
            "frame-active", activeFrame === "booth");
    }

    function buildPlaceholderRig() {
        const skin = new THREE.MeshStandardMaterial({ color: 0xe6b193, roughness: 0.65 });
        const hairMat = new THREE.MeshStandardMaterial({ color: 0x33241c, roughness: 0.5 });
        const blouse = new THREE.MeshStandardMaterial({ color: 0x14655c, roughness: 0.7 });
        const slacks = new THREE.MeshStandardMaterial({ color: 0x1c2434, roughness: 0.8 });
        const lipMat = new THREE.MeshStandardMaterial({ color: 0xa65a52, roughness: 0.55 });
        const mouthInnerMat = new THREE.MeshBasicMaterial({ color: 0x40191c });
        const white = new THREE.MeshStandardMaterial({ color: 0xf4f2ee, roughness: 0.25 });
        const irisMat = new THREE.MeshBasicMaterial({ color: 0x4f6b52 });
        const pupilMat = new THREE.MeshBasicMaterial({ color: 0x101010 });

        const figure = new THREE.Group();

        // Body: deliberately stylized -- this placeholder exists to prove the
        // pipeline and frame the camera, not to impersonate the CC5 build.
        const hips = new THREE.Mesh(
            new THREE.CylinderGeometry(0.13, 0.105, 0.86, 24), slacks);
        hips.position.y = 0.52;
        figure.add(hips);
        const torso = new THREE.Mesh(
            new THREE.CapsuleGeometry(0.145, 0.34, 8, 24), blouse);
        torso.position.y = 1.18;
        figure.add(torso);
        for (const side of [-1, 1]) {
            const arm = new THREE.Mesh(
                new THREE.CapsuleGeometry(0.038, 0.52, 6, 16), blouse);
            arm.position.set(side * 0.2, 1.12, 0);
            arm.rotation.z = side * 0.06;
            figure.add(arm);
        }
        const neck = new THREE.Mesh(
            new THREE.CylinderGeometry(0.042, 0.05, 0.09, 20), skin);
        neck.position.y = 1.5;
        figure.add(neck);

        const head = new THREE.Group();
        head.position.y = 1.62;
        figure.add(head);

        const skull = new THREE.Mesh(new THREE.SphereGeometry(0.105, 40, 32), skin);
        skull.scale.set(0.92, 1.06, 0.94);
        head.add(skull);

        const hairBack = new THREE.Mesh(
            new THREE.SphereGeometry(0.112, 40, 32, 0, Math.PI * 2, 0, Math.PI * 0.62),
            hairMat);
        hairBack.scale.set(0.98, 1.08, 0.98);
        hairBack.position.set(0, 0.012, -0.014);
        head.add(hairBack);
        const bun = new THREE.Mesh(new THREE.SphereGeometry(0.045, 24, 18), hairMat);
        bun.position.set(0, 0.05, -0.1);
        head.add(bun);

        const nose = new THREE.Mesh(new THREE.ConeGeometry(0.011, 0.03, 12), skin);
        nose.rotation.x = Math.PI / 2;
        nose.position.set(0, -0.012, 0.098);
        head.add(nose);

        const eyes = { left: null, right: null, lids: [] };
        for (const side of [-1, 1]) {
            const eyeGroup = new THREE.Group();
            eyeGroup.position.set(side * 0.037, 0.018, 0.078);
            const ball = new THREE.Mesh(new THREE.SphereGeometry(0.0145, 24, 18), white);
            eyeGroup.add(ball);
            const iris = new THREE.Mesh(new THREE.CircleGeometry(0.008, 20), irisMat);
            iris.position.z = 0.0142;
            eyeGroup.add(iris);
            const pupil = new THREE.Mesh(new THREE.CircleGeometry(0.0038, 16), pupilMat);
            pupil.position.z = 0.0146;
            eyeGroup.add(pupil);
            const lid = new THREE.Mesh(
                new THREE.SphereGeometry(0.0165, 24, 12, 0, Math.PI * 2, 0, Math.PI * 0.55),
                skin);
            lid.rotation.x = -0.35;
            eyeGroup.add(lid);
            const brow = new THREE.Mesh(
                new THREE.BoxGeometry(0.034, 0.0052, 0.006), hairMat);
            brow.position.set(0, 0.028, 0.004);
            brow.rotation.z = side * -0.12;
            eyeGroup.add(brow);
            head.add(eyeGroup);
            eyes[side === -1 ? "left" : "right"] = eyeGroup;
            eyes.lids.push({ lid: lid, brow: brow, side: side });
        }

        // Jaw pivots near the ear line; the chin and lower lip ride on it so
        // jawOpen reads as a real jaw, not a scaling mouth decal.
        const jaw = new THREE.Group();
        jaw.position.set(0, -0.02, 0.01);
        head.add(jaw);
        const chin = new THREE.Mesh(new THREE.SphereGeometry(0.062, 28, 20), skin);
        chin.scale.set(1.05, 0.72, 0.9);
        chin.position.set(0, -0.045, 0.028);
        jaw.add(chin);

        const mouth = new THREE.Group();
        mouth.position.set(0, -0.034, 0.089);
        head.add(mouth);
        const mouthInner = new THREE.Mesh(
            new THREE.CircleGeometry(0.0135, 24), mouthInnerMat);
        mouthInner.position.z = -0.002;
        mouth.add(mouthInner);
        const upperLip = new THREE.Mesh(
            new THREE.CapsuleGeometry(0.0042, 0.03, 4, 12), lipMat);
        upperLip.rotation.z = Math.PI / 2;
        upperLip.position.y = 0.008;
        mouth.add(upperLip);
        // Lower lip is parented to the jaw so it follows jawOpen.
        const lowerLip = new THREE.Mesh(
            new THREE.CapsuleGeometry(0.005, 0.028, 4, 12), lipMat);
        lowerLip.rotation.z = Math.PI / 2;
        lowerLip.position.set(0, -0.008, 0.079);
        jaw.add(lowerLip);

        const group = figure;

        function apply(now) {
            const open = weight("jawOpen");
            const close = weight("mouthClose");
            const pucker = weight("mouthPucker");
            const funnel = weight("mouthFunnel");
            const smile = (weight("mouthSmileLeft") + weight("mouthSmileRight")) / 2;
            const press = (weight("mouthPressLeft") + weight("mouthPressRight")) / 2;

            jaw.rotation.x = open * 0.38;

            const width = Math.max(0.35, 1 + smile * 0.35 - pucker * 0.5 - funnel * 0.3);
            const openness = Math.max(0.12, open * 1.9 * (1 - close) + funnel * 0.35);
            mouth.scale.set(width, 1, 1);
            mouthInner.scale.set(1, Math.min(2.4, openness), 1);
            mouth.position.z = 0.089 + pucker * 0.009 + funnel * 0.006;
            mouth.position.y = -0.034 + smile * 0.004;
            // The capsule is rotated 90 degrees, so its thickness is local X.
            upperLip.scale.x = 1 - press * 0.35;
            lowerLip.position.y = -0.008 - weight("mouthRollLower") * 0.004;

            const blinkL = weight("eyeBlinkLeft");
            const blinkR = weight("eyeBlinkRight");
            for (const entry of eyes.lids) {
                const blink = entry.side === -1 ? blinkL : blinkR;
                entry.lid.rotation.x = -0.35 + blink * 1.25;
                entry.brow.position.y = 0.028 + weight("browInnerUp") * 0.006;
            }
            for (const side of ["left", "right"]) {
                eyes[side].rotation.y = idle.gaze.x * 0.35;
                eyes[side].rotation.x = -idle.gaze.y * 0.3;
            }

            // Breathing sway; a little more presence in booth framing.
            const sway = activeFrame === "booth" ? 1 : 0.55;
            const t = now / 1000;
            head.rotation.y = Math.sin(t * 0.31) * 0.045 * sway + idle.gaze.x * 0.12;
            head.rotation.x = Math.sin(t * 0.23) * 0.02 * sway - idle.gaze.y * 0.06;
            head.rotation.z = Math.sin(t * 0.17) * 0.014 * sway;
            torso.position.y = 1.18 + Math.sin(t * 0.9) * 0.0035;
            group.rotation.y = Math.sin(t * 0.11) * 0.02 * sway;
        }

        return { group: group, apply: apply };
    }

    function buildGltfRig(gltfScene) {
        // Bind every mesh that carries morph targets; drive them by ARKit
        // name each frame. Unknown names are left alone, missing names are
        // reported once so a bad CC5 export is loud, not subtly frozen.
        const morphMeshes = [];
        gltfScene.traverse(function (node) {
            if (node.isMesh && node.morphTargetDictionary) {
                morphMeshes.push(node);
            }
        });
        const found = new Set();
        for (const mesh of morphMeshes) {
            for (const name of Object.keys(mesh.morphTargetDictionary)) {
                found.add(name);
            }
        }
        console.info(
            "MAE avatar: model exposes " + found.size + " morph targets:",
            Array.from(found).sort().join(", "));
        for (const key of MOUTH_KEYS.concat(["eyeBlinkLeft", "eyeBlinkRight"])) {
            if (!found.has(key)) {
                console.warn("MAE avatar: model is missing ARKit shape '" + key +
                    "' -- was the export made with the ARKit profile on?");
            }
        }

        function apply(now) {
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
        }
        return { group: gltfScene, apply: apply };
    }

    function initScene() {
        try {
            renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
        } catch (error) {
            console.warn("MAE avatar: WebGL unavailable, using portrait fallback.", error);
            fallback.style.display = "flex";
            return false;
        }
        renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
        renderer.setSize(window.innerWidth, window.innerHeight);
        stage.appendChild(renderer.domElement);

        scene = new THREE.Scene();
        scene.background = new THREE.Color(0x0b1220);
        scene.fog = new THREE.Fog(0x0b1220, 4, 9);

        camera = new THREE.PerspectiveCamera(
            26, window.innerWidth / window.innerHeight, 0.05, 20);

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

        const floor = new THREE.Mesh(
            new THREE.CircleGeometry(1.6, 48),
            new THREE.MeshStandardMaterial({ color: 0x101a2e, roughness: 0.9 }));
        floor.rotation.x = -Math.PI / 2;
        scene.add(floor);

        rig = buildPlaceholderRig();
        scene.add(rig.group);

        // The CC5 character drops in here when Phase 0 delivers it; until
        // then the 404 is expected and the placeholder stays.
        new GLTFLoader().load(
            "/static/models/mae.glb",
            function (gltf) {
                scene.remove(rig.group);
                rig = buildGltfRig(gltf.scene);
                scene.add(rig.group);
                addStatusLine("Character model loaded.");
            },
            undefined,
            function () {
                console.info("MAE avatar: no /static/models/mae.glb yet; placeholder active.");
            });

        // Portrait aspect (the 512x1536 booth panel) defaults to full body.
        applyFrame(window.innerWidth / window.innerHeight < 0.55 ? "booth" : "console");

        window.addEventListener("resize", function () {
            camera.aspect = window.innerWidth / window.innerHeight;
            camera.updateProjectionMatrix();
            renderer.setSize(window.innerWidth, window.innerHeight);
        });

        renderer.setAnimationLoop(function (now) {
            idleStep(now);
            const targets = activeVisemeTargets();
            // Attack faster than release so consonant closures register.
            for (const key of MOUTH_KEYS) {
                const target = targets[key] || 0;
                const current = weight(key);
                const alpha = target > current ? 0.45 : 0.28;
                setWeight(key, current + (target - current) * alpha);
            }
            rig.apply(now);
            renderer.render(scene, camera);
        });
        return true;
    }

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
            applyFrame(button.dataset.frame);
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

    initScene();
    loadIdentity();
    loadVoiceStatus();
    window.setInterval(loadVoiceStatus, 60000);
    addStatusLine("MAE is ready. Ask about active calls, wait times, or coverage.");
})();
