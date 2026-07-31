(function () {
    "use strict";

    const form = document.getElementById("nova-form");
    const input = document.getElementById("nova-question");
    const send = document.getElementById("nova-send");
    const messages = document.getElementById("nova-messages");
    const thinking = document.getElementById("nova-thinking");
    const voiceToggle = document.getElementById("nova-voice-toggle");
    const voiceSession = document.getElementById("nova-voice-session");
    const voiceStop = document.getElementById("nova-voice-stop");
    const voiceState = document.getElementById("nova-voice-state");
    const voiceDetail = document.getElementById("nova-voice-detail");
    const player = document.getElementById("nova-voice-player");
    const history = [];
    let busy = false;
    let voiceReady = false;
    let voiceMode = false;
    let stream = null;
    let audioContext = null;
    let analyser = null;
    let recorder = null;
    let chunks = [];
    let voiceFrame = null;
    let cycleStarted = 0;
    let speechStarted = 0;
    let lastSpeechAt = 0;
    let speechDetected = false;
    let discardRecording = false;
    let audioUrl = "";

    function setStatus(online, text) {
        const card = document.getElementById("nova-ai-status");
        card.classList.toggle("is-online", Boolean(online));
        card.classList.toggle("is-offline", !online);
        card.querySelector("strong").textContent = text;
    }

    function setVoiceState(state, title, detail) {
        voiceSession.classList.remove("is-listening", "is-hearing", "is-processing", "is-speaking", "is-error");
        if (state) voiceSession.classList.add(`is-${state}`);
        voiceState.textContent = title;
        voiceDetail.textContent = detail;
    }

    function microphoneEnabled(enabled) {
        if (!stream) return;
        stream.getAudioTracks().forEach(function (track) { track.enabled = enabled; });
    }

    async function loadStatus() {
        try {
            const [novaResponse, voiceResponse] = await Promise.all([
                fetch("/api/nga911/v1/nova/status", {cache: "no-store"}),
                fetch("/api/voice/status", {cache: "no-store"})
            ]);
            const nova = await novaResponse.json();
            const voice = await voiceResponse.json();
            setStatus(nova.connected && nova.model_available, nova.connected ? nova.model : "Unavailable");
            voiceReady = Boolean(voiceResponse.ok && voice.connected && voice.tts && voice.tts.ready && voice.stt && voice.stt.ready);
        } catch (error) {
            setStatus(false, "Unavailable");
            voiceReady = false;
        }
        voiceToggle.disabled = !voiceReady;
        if (!voiceReady) voiceToggle.querySelector("small").textContent = "Voice service unavailable";
    }

    function addMessage(role, text, payload) {
        const article = document.createElement("article");
        article.className = `mae-message mae-message-${role}`;
        const avatar = document.createElement("div");
        avatar.className = "mae-avatar" + (role === "assistant" ? " nova-avatar" : "");
        avatar.innerHTML = role === "assistant" ? '<i class="bi bi-stars"></i>' : '<i class="bi bi-person-fill"></i>';
        const bubble = document.createElement("div");
        bubble.className = "mae-bubble";
        const name = document.createElement("div");
        name.className = "mae-message-name";
        name.textContent = role === "assistant" ? "NOVA" : "USER";
        const copy = document.createElement("p");
        copy.textContent = text;
        bubble.append(name, copy);
        if (role === "assistant") {
            if (payload && payload.assurance) {
                const assurance = document.createElement("div");
                assurance.className = "mae-assurance mae-assurance-supported";
                assurance.innerHTML = `<strong>${payload.assurance.label}</strong><small>${payload.assurance.detail}</small>`;
                bubble.append(assurance);
            }
            const actions = document.createElement("div");
            actions.className = "nova-report-actions";
            const listen = document.createElement("button");
            listen.type = "button";
            listen.innerHTML = '<i class="bi bi-volume-up-fill"></i> Listen';
            listen.addEventListener("click", async function () {
                listen.disabled = true;
                try { await speak(text); } catch (error) { window.alert(error.message); }
                finally { listen.disabled = false; }
            });
            const copyButton = document.createElement("button");
            copyButton.type = "button";
            copyButton.innerHTML = '<i class="bi bi-clipboard"></i> Copy report';
            copyButton.addEventListener("click", function () { navigator.clipboard.writeText(text); });
            actions.append(listen, copyButton);
            bubble.append(actions);
        }
        article.append(avatar, bubble);
        messages.append(article);
        messages.scrollTop = messages.scrollHeight;
    }

    async function speak(text) {
        if (voiceMode) {
            microphoneEnabled(false);
            setVoiceState("speaking", "NOVA is speaking", "The microphone is paused to prevent an echo.");
        }
        const response = await fetch("/api/voice/speech", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            cache: "no-store",
            body: JSON.stringify({text: String(text).slice(0, 2500), voice: "af_kore", speed: 0.97, response_format: "mp3"})
        });
        if (!response.ok) throw new Error("NOVA could not generate speech.");
        if (audioUrl) URL.revokeObjectURL(audioUrl);
        audioUrl = URL.createObjectURL(await response.blob());
        player.src = audioUrl;
        await new Promise(function (resolve, reject) {
            player.onended = resolve;
            player.onerror = function () { reject(new Error("The browser could not play NOVA's voice.")); };
            const playPromise = player.play();
            if (playPromise) playPromise.catch(reject);
        });
    }

    async function transcribe(blob) {
        const data = new FormData();
        data.append("file", blob, "nova-question.webm");
        const response = await fetch("/api/voice/transcribe", {method: "POST", cache: "no-store", body: data});
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || "NOVA could not transcribe the question.");
        return String(payload.text || "").trim();
    }

    function stopCycle(discard) {
        discardRecording = Boolean(discard);
        if (voiceFrame) window.cancelAnimationFrame(voiceFrame);
        voiceFrame = null;
        if (recorder && recorder.state === "recording") recorder.stop();
    }

    function monitorLevel() {
        if (!voiceMode || !recorder || recorder.state !== "recording" || !analyser) return;
        const samples = new Uint8Array(analyser.fftSize);
        analyser.getByteTimeDomainData(samples);
        let energy = 0;
        samples.forEach(function (sample) { const normalized = (sample - 128) / 128; energy += normalized * normalized; });
        const volume = Math.sqrt(energy / samples.length);
        const now = Date.now();
        if (volume >= 0.032) {
            if (!speechDetected) {
                speechDetected = true;
                speechStarted = now;
                setVoiceState("hearing", "I hear you", "Finish your question and pause naturally.");
            }
            lastSpeechAt = now;
        }
        const enoughSpeech = speechDetected && now - speechStarted >= 450;
        const naturalPause = enoughSpeech && now - lastSpeechAt >= 1050;
        const maximumUtterance = speechDetected && now - speechStarted >= 30000;
        const emptyCycle = !speechDetected && now - cycleStarted >= 45000;
        if (naturalPause || maximumUtterance) return stopCycle(false);
        if (emptyCycle) return stopCycle(true);
        voiceFrame = window.requestAnimationFrame(monitorLevel);
    }

    function beginCycle() {
        if (!voiceMode || busy || !stream) return;
        microphoneEnabled(true);
        chunks = [];
        cycleStarted = Date.now();
        speechStarted = 0;
        lastSpeechAt = 0;
        speechDetected = false;
        discardRecording = false;
        const options = MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? {mimeType: "audio/webm;codecs=opus"} : {};
        recorder = new MediaRecorder(stream, options);
        recorder.addEventListener("dataavailable", function (event) { if (event.data.size) chunks.push(event.data); });
        recorder.addEventListener("stop", async function () {
            const shouldAsk = voiceMode && speechDetected && !discardRecording;
            const recording = new Blob(chunks, {type: recorder.mimeType || "audio/webm"});
            microphoneEnabled(false);
            if (!shouldAsk) {
                if (voiceMode && !busy) window.setTimeout(beginCycle, 150);
                return;
            }
            setVoiceState("processing", "Understanding your question", "Local speech recognition is processing the recording.");
            try {
                const question = await transcribe(recording);
                if (question.length < 2) {
                    setVoiceState("listening", "I did not catch that", "Please ask the question again.");
                    return window.setTimeout(beginCycle, 500);
                }
                await ask(question, true);
            } catch (error) {
                setVoiceState("error", "Voice request failed", error.message);
                if (voiceMode) window.setTimeout(beginCycle, 1200);
            }
        });
        recorder.start(250);
        setVoiceState("listening", "Listening", "Ask NOVA a question, then pause naturally.");
        voiceFrame = window.requestAnimationFrame(monitorLevel);
    }

    async function ask(question, autoSpeak) {
        if (!question) return;
        const recent = history.slice(-4);
        addMessage("user", question);
        history.push({role: "user", content: question});
        busy = true;
        thinking.hidden = false;
        send.disabled = true;
        input.disabled = true;
        if (voiceMode) setVoiceState("processing", "NOVA is reviewing the intelligence layer", "Preparing a grounded answer.");
        try {
            const response = await fetch("/api/nga911/v1/nova/chat", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                cache: "no-store",
                body: JSON.stringify({question: question, history: recent})
            });
            const payload = await response.json();
            if (!response.ok) throw new Error(payload.detail || "NOVA could not answer.");
            addMessage("assistant", payload.answer, payload);
            history.push({role: "assistant", content: payload.answer});
            if (autoSpeak) await speak(payload.answer);
        } catch (error) {
            addMessage("assistant", `I could not complete that inquiry. ${error.message}`);
            if (voiceMode) setVoiceState("error", "NOVA could not answer", error.message);
        } finally {
            busy = false;
            thinking.hidden = true;
            send.disabled = false;
            input.disabled = false;
        }
        if (voiceMode) beginCycle(); else input.focus();
    }

    async function startVoiceMode() {
        if (!voiceReady || voiceMode) return;
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || typeof MediaRecorder === "undefined") {
            window.alert("This browser does not support NOVA voice mode.");
            return;
        }
        voiceSession.hidden = false;
        setVoiceState("processing", "Requesting microphone", "Allow microphone access when your browser asks.");
        stream = await navigator.mediaDevices.getUserMedia({audio: {echoCancellation: true, noiseSuppression: true, autoGainControl: true}});
        audioContext = new (window.AudioContext || window.webkitAudioContext)();
        await audioContext.resume();
        analyser = audioContext.createAnalyser();
        analyser.fftSize = 512;
        audioContext.createMediaStreamSource(stream).connect(analyser);
        voiceMode = true;
        voiceToggle.classList.add("is-active");
        voiceToggle.querySelector("strong").textContent = "Voice mode active";
        voiceToggle.querySelector("small").textContent = "NOVA is ready to converse";
        await speak("NOVA voice mode is ready. What N G A nine one one intelligence question can I answer?");
        if (voiceMode) beginCycle();
    }

    function endVoiceMode() {
        voiceMode = false;
        stopCycle(true);
        player.pause();
        if (stream) stream.getTracks().forEach(function (track) { track.stop(); });
        stream = null;
        if (audioContext) audioContext.close().catch(function () {});
        audioContext = null;
        analyser = null;
        voiceSession.hidden = true;
        voiceToggle.classList.remove("is-active");
        voiceToggle.querySelector("strong").textContent = "Ask by voice";
        voiceToggle.querySelector("small").textContent = "Uses the private local voice stack";
    }

    form.addEventListener("submit", function (event) {
        event.preventDefault();
        const question = input.value.trim();
        input.value = "";
        if (voiceMode) stopCycle(true);
        ask(question, voiceMode);
    });
    input.addEventListener("keydown", function (event) {
        if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); form.requestSubmit(); }
    });
    document.querySelectorAll("[data-nova-prompt]").forEach(function (button) {
        button.addEventListener("click", function () { input.value = button.dataset.novaPrompt; input.focus(); });
    });
    voiceToggle.addEventListener("click", function () {
        if (voiceMode) endVoiceMode(); else startVoiceMode().catch(function (error) { endVoiceMode(); window.alert(error.message); });
    });
    voiceStop.addEventListener("click", endVoiceMode);
    loadStatus();
}());
