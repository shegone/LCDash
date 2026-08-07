(function () {
    "use strict";

    const form = document.getElementById("mae-form");
    const questionInput = document.getElementById("mae-question");
    const messages = document.getElementById("mae-messages");
    const thinking = document.getElementById("mae-thinking");
    const sendButton = document.getElementById("mae-send");
    const voiceToggle = document.getElementById("mae-voice-toggle");
    const voiceSession = document.getElementById("mae-voice-session");
    const voiceStop = document.getElementById("mae-voice-stop");
    const voiceState = document.getElementById("mae-voice-state");
    const voiceDetail = document.getElementById("mae-voice-detail");
    const voicePlayer = document.getElementById("mae-voice-player");
    const maeAvatarSource = "/static/img/mae/mae-neutral.jpg";
    const maeRequestTimeoutMs = 130000;
    const history = [];
    const entities = {
        cfs_numbers: [],
        unit_numbers: [],
        stations: [],
        addresses: [],
        incidents: []
    };
    let maeBusy = false;
    let voiceReady = false;
    let voiceModeActive = false;
    let microphoneStream = null;
    let audioContext = null;
    let analyser = null;
    let mediaRecorder = null;
    let voiceChunks = [];
    let voiceFrame = null;
    let voiceCycleStarted = 0;
    let speechStarted = 0;
    let lastSpeechAt = 0;
    let speechDetected = false;
    let discardRecording = false;
    let cloudVoiceName = "";
    let sentenceSpeechAvailable = true;
    let advisoryStreamAvailable = true;

    const maeShell = document.querySelector(".mae-shell");
    const cloudMode = maeShell?.dataset.cloudMode === "true";
    const advisoryReady = maeShell?.dataset.advisoryReady === "true";
    if (cloudMode) {
        document.querySelectorAll("[data-mae-prompt], .mae-prompt-folder").forEach(function (control) {
            control.hidden = true;
        });
    }
    if (cloudMode && !advisoryReady) {
        questionInput.disabled = true;
        questionInput.placeholder = "Approved citation source unavailable";
        sendButton.disabled = true;
        voiceToggle.disabled = true;
        document.querySelectorAll("[data-mae-prompt]").forEach(function (button) {
            button.disabled = true;
        });
        setStatusUnavailable();
        return;
    }

    function setStatusUnavailable() {
        ["mae-ai-status", "mae-db-status", "mae-cad-status"].forEach(function (cardId) {
            const card = document.getElementById(cardId);
            if (!card) return;
            card.classList.add("is-offline");
            const value = card.querySelector("strong");
            if (value) value.textContent = "Unavailable";
        });
    }

    function setStatus(cardId, online, text) {
        const card = document.getElementById(cardId);
        if (!card) return;
        card.classList.toggle("is-online", Boolean(online));
        card.classList.toggle("is-offline", !online);
        const value = card.querySelector("strong");
        if (value) value.textContent = text;
    }

    async function loadStatus() {
        try {
            const response = await fetch("/api/mae/status", {cache: "no-store"});
            if (!response.ok) throw new Error("Status unavailable");
            const status = await response.json();
            setStatus(
                "mae-ai-status",
                status.local_ai.connected,
                status.local_ai.connected
                    ? `Online · ${status.local_ai.model}`
                    : "Offline"
            );
            setStatus(
                "mae-db-status",
                status.database.configured && status.database.connected,
                status.database.configured && status.database.connected
                    ? "Connected"
                    : "Unavailable"
            );
            setStatus(
                "mae-cad-status",
                status.centralsquare.connected,
                status.centralsquare.mode || "Unavailable"
            );
        } catch (error) {
            setStatus("mae-ai-status", false, "Unavailable");
            setStatus("mae-db-status", false, "Unavailable");
            setStatus("mae-cad-status", false, "Unavailable");
        }
    }

    async function loadVoiceStatus() {
        if (!voiceToggle) return;
        try {
            const response = await fetch("/api/voice/status", {cache: "no-store"});
            const status = await response.json();
            voiceReady = Boolean(
                response.ok &&
                status.connected &&
                status.tts &&
                status.tts.ready &&
                status.stt &&
                status.stt.ready
            );
        } catch (error) {
            voiceReady = false;
        }

        voiceToggle.disabled = !voiceReady;
        voiceToggle.title = voiceReady
            ? "Start a private voice conversation with MAE"
            : "Conversational voice is disabled until speech synthesis and transcription are both ready";
        if (!voiceReady) {
            voiceToggle.querySelector("small").textContent = "Unavailable - transcription gate not complete";
        }
    }

    function setVoiceState(state, title, detail) {
        if (!voiceSession) return;
        voiceSession.classList.remove(
            "is-listening",
            "is-hearing",
            "is-processing",
            "is-speaking",
            "is-error"
        );
        if (state) voiceSession.classList.add(`is-${state}`);
        voiceState.textContent = title;
        voiceDetail.textContent = detail;
    }

    function setMicrophoneEnabled(enabled) {
        if (!microphoneStream) return;
        microphoneStream.getAudioTracks().forEach(function (track) {
            track.enabled = enabled;
        });
    }

    // ---- Sentence-level speech -------------------------------------------
    // Source URLs and citations are rendered separately and must never be
    // spoken. Every chunk is sanitized here and again on the server before it
    // reaches Polly.
    const SPEECH_ABBREVIATIONS = new Set([
        "mr", "mrs", "ms", "dr", "prof", "rev", "gen", "adm", "col", "maj",
        "sgt", "lt", "capt", "cpt", "cmdr", "ofc", "det", "dep", "supt", "insp",
        "st", "ave", "rd", "blvd", "hwy", "ln", "ct", "apt", "ste", "bldg",
        "sta", "stn", "rm", "unit", "bat", "eng", "med", "sq",
        "no", "nos", "dept", "div", "est", "approx", "fig", "figs", "sec",
        "secs", "art", "ch", "chap", "p", "pp", "vol", "eds", "attn", "ref",
        "inc", "corp", "co", "ltd", "llc", "assn", "govt", "ext",
        "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct",
        "nov", "dec", "mon", "tue", "tues", "wed", "thu", "thur", "thurs",
        "fri", "sat", "sun", "vs", "etc", "al", "cf", "viz", "resp",
        "e.g", "i.e", "a.m", "p.m", "u.s", "u.s.a", "u.k"
    ]);
    const SPEECH_TERMINATORS = ".!?";
    const SPEECH_CLOSERS = "\"')]}»”’";
    const SPEECH_GROUP_TARGET = 180;
    const SPEECH_MAX_CHARS = 2400;

    function answerForSpeech(text) {
        return String(text || "")
            .split(/^\s*sources\s*:?\s*$/im)[0]
            .replace(/\b(?:https?|s3|ftp|file):\/\/\S+/gi, " ")
            .replace(/\b[\w.+-]+@[\w-]+\.[\w.-]+\b/g, " ")
            .replace(/\b(?:www\.\S+|[\w-]+\.(?:com|org|net|gov|edu|io|us|mil))\b/gi, " ")
            .replace(/\[[^\]]*?(?:page|p\.)\s*\d+[^\]]*?\]/gi, " ")
            .replace(/\[\s*\d+(?:\s*[,-]\s*\d+)*\s*\]/g, " ")
            .replace(/\((?:source|citation|see)\b[^)]*\)/gi, " ")
            .replace(/^\s*[-•–]\s+/gm, "")
            .replace(/[*_`#>|]+/g, " ")
            .replace(/\s+/g, " ")
            .trim();
    }

    function isSpeechSoftStop(source, dotIndex, end) {
        const prefix = source.slice(0, dotIndex);
        const tokens = prefix.split(/\s+/);
        const token = tokens.length ? tokens[tokens.length - 1] : "";
        const clean = token.toLowerCase().replace(/^[([{"'“‘]+/, "");
        if (clean && SPEECH_ABBREVIATIONS.has(clean)) return true;
        if (clean.length === 1 && /[a-z]/.test(clean)) return true;
        if (/^(?:[a-z]\.)+[a-z]$/.test(clean)) return true;
        const line = prefix.split("\n").pop();
        if (token && /^\s*(?:\d{1,3}|[A-Za-z]|[ivxIVX]{1,4})$/.test(line)) return true;
        const tail = source.slice(end).replace(/^\s+/, "");
        if (!tail) return false;
        // A lower-case continuation almost always means the period belonged to
        // an abbreviation this list does not know about.
        if (/^[a-z]/.test(tail)) return true;
        // "Sta. 3", "Rm. 12", "Ch. 5" - a short word followed by a number.
        if (/^\d/.test(tail) && /^[a-z]+$/.test(clean) && clean.length <= 4) return true;
        return false;
    }

    function boundSpeechChunk(chunk) {
        const parts = [];
        let rest = chunk;
        while (rest.length > SPEECH_MAX_CHARS) {
            const window = rest.slice(0, SPEECH_MAX_CHARS);
            let cut = window.lastIndexOf(" ");
            if (cut < SPEECH_MAX_CHARS / 4) cut = SPEECH_MAX_CHARS;
            parts.push(rest.slice(0, cut).trim());
            rest = rest.slice(cut).trim();
        }
        if (rest) parts.push(rest);
        return parts.filter(Boolean);
    }

    // Incremental sentence chunker. A chunk is released only once its end has
    // been confirmed by a following whitespace character, so a sentence can
    // never be cut mid-word, mid-decimal, or mid-abbreviation. The first chunk
    // is released on its own so audio can start immediately; later sentences
    // are grouped so the cadence stays natural and Polly calls stay bounded.
    function createSpeechChunker() {
        let buffer = "";
        let carry = "";
        let group = "";
        let emitted = 0;
        let stopped = false;

        function boundaryIndex(text, final) {
            const length = text.length;
            let index = 0;
            while (index < length) {
                const char = text[index];
                if (char === "\n") return index + 1;
                if (SPEECH_TERMINATORS.indexOf(char) !== -1) {
                    let end = index + 1;
                    while (end < length && SPEECH_TERMINATORS.indexOf(text[end]) !== -1) {
                        end += 1;
                    }
                    while (end < length && SPEECH_CLOSERS.indexOf(text[end]) !== -1) {
                        end += 1;
                    }
                    if (end >= length) return final ? length : -1;
                    if (!/\s/.test(text[end])) {
                        index = end;
                        continue;
                    }
                    if (char === "." && isSpeechSoftStop(text, index, end)) {
                        index = end;
                        continue;
                    }
                    return end;
                }
                index += 1;
            }
            return (final && text.trim()) ? length : -1;
        }

        function process(text, final) {
            const sentences = [];
            if (stopped) {
                buffer = "";
            } else {
                buffer += text;
                if (final && carry) {
                    buffer = (carry + " " + buffer).trim();
                    carry = "";
                }
                while (buffer) {
                    const index = boundaryIndex(buffer, final);
                    if (index < 0) break;
                    let piece = buffer.slice(0, index).trim();
                    buffer = buffer.slice(index).replace(/^\s+/, "");
                    if (!piece) continue;
                    if (carry) {
                        piece = (carry + " " + piece).trim();
                        carry = "";
                    }
                    // A Sources block is rendered separately and is never spoken.
                    if (/^\s*sources\s*:?\s*$/i.test(piece)) {
                        stopped = true;
                        buffer = "";
                        break;
                    }
                    if ((piece.match(/[A-Za-z]/g) || []).length < 2 && !final) {
                        carry = piece;
                        continue;
                    }
                    sentences.push(piece);
                }
            }
            const ready = [];
            sentences.forEach(function (sentence) {
                if (emitted === 0 && !group) {
                    ready.push(sentence);
                    emitted += 1;
                    return;
                }
                group = (group + " " + sentence).trim();
                if (group.length >= SPEECH_GROUP_TARGET) {
                    ready.push(group);
                    group = "";
                    emitted += 1;
                }
            });
            if (final && group) {
                ready.push(group);
                group = "";
                emitted += 1;
            }
            const bounded = [];
            ready.forEach(function (item) {
                boundSpeechChunk(item).forEach(function (part) { bounded.push(part); });
            });
            return bounded;
        }

        return {
            feed: function (text) { return process(String(text || ""), false); },
            flush: function () { return process("", true); }
        };
    }

    function splitIntoSpeechChunks(text) {
        const chunker = createSpeechChunker();
        const chunks = chunker.feed(String(text || ""));
        chunker.flush().forEach(function (item) { chunks.push(item); });
        return chunks;
    }

    async function requestSpeechAudio(text, signal) {
        const spokenText = String(text || "").trim().slice(0, SPEECH_MAX_CHARS);
        if (!spokenText) return null;
        if (cloudMode && sentenceSpeechAvailable) {
            const sentenceResponse = await fetch("/api/cloud-ai/speech/sentence", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                cache: "no-store",
                signal: signal,
                body: JSON.stringify({
                    text: spokenText,
                    persona: "mae",
                    voice: cloudVoiceName || ""
                })
            });
            if (sentenceResponse.ok) return sentenceResponse.blob();
            if (sentenceResponse.status === 404 || sentenceResponse.status === 405) {
                sentenceSpeechAvailable = false;
            } else {
                const detail = await sentenceResponse.json().catch(function () { return {}; });
                throw new Error(detail.detail || "MAE could not generate speech.");
            }
        }
        const response = await fetch("/api/voice/speech", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            cache: "no-store",
            signal: signal,
            body: JSON.stringify({
                text: spokenText,
                voice: cloudMode
                    ? (cloudVoiceName || "Joanna")
                    : "mae-synthetic-female",
                speed: 1.0,
                response_format: "mp3"
            })
        });
        if (!response.ok) {
            const payload = await response.json().catch(function () { return {}; });
            throw new Error(payload.detail || "MAE could not generate speech.");
        }

        return response.blob();
    }

    // Sequential audio queue: exactly one clip plays at a time, synthesis for
    // the next chunk overlaps playback of the current one, one failed chunk is
    // skipped without breaking the chain, and a new session abandons the old
    // queue outright.
    const speech = (function () {
        let sessionId = 0;
        let controller = null;
        let synthesisChain = Promise.resolve();
        let playbackChain = Promise.resolve();
        let objectUrl = "";
        let releaseActivePlayback = null;
        let queued = 0;
        let played = 0;

        function isCurrent(id) {
            return id === sessionId && id > 0;
        }

        function revoke() {
            if (objectUrl) {
                URL.revokeObjectURL(objectUrl);
                objectUrl = "";
            }
        }

        function resetPlayer() {
            if (releaseActivePlayback) {
                const release = releaseActivePlayback;
                releaseActivePlayback = null;
                release();
            }
            if (!voicePlayer) return;
            try {
                voicePlayer.pause();
            } catch (error) {
                // A player that was never started cannot be paused; ignore.
            }
            voicePlayer.onended = null;
            voicePlayer.onerror = null;
            voicePlayer.removeAttribute("src");
            try {
                voicePlayer.load();
            } catch (error) {
                // Reloading an empty player is a no-op in some browsers.
            }
        }

        function begin() {
            sessionId += 1;
            if (controller) controller.abort();
            controller = new AbortController();
            synthesisChain = Promise.resolve();
            playbackChain = Promise.resolve();
            queued = 0;
            played = 0;
            resetPlayer();
            revoke();
            return sessionId;
        }

        function stop() {
            sessionId += 1;
            if (controller) {
                controller.abort();
                controller = null;
            }
            synthesisChain = Promise.resolve();
            playbackChain = Promise.resolve();
            resetPlayer();
            revoke();
        }

        function play(audioBlob, id) {
            if (!voicePlayer || !audioBlob || !isCurrent(id)) return Promise.resolve();
            revoke();
            objectUrl = URL.createObjectURL(audioBlob);
            voicePlayer.src = objectUrl;
            return new Promise(function (resolve) {
                let settled = false;
                function finish() {
                    if (settled) return;
                    settled = true;
                    if (releaseActivePlayback === finish) releaseActivePlayback = null;
                    voicePlayer.onended = null;
                    voicePlayer.onerror = null;
                    resolve();
                }
                releaseActivePlayback = finish;
                voicePlayer.onended = function () {
                    played += 1;
                    finish();
                };
                voicePlayer.onerror = finish;
                const playPromise = voicePlayer.play();
                if (playPromise && playPromise.catch) playPromise.catch(finish);
            });
        }

        function enqueue(id, text) {
            if (!isCurrent(id)) return;
            const clean = answerForSpeech(text);
            if (!clean) return;
            queued += 1;
            const signal = controller ? controller.signal : undefined;
            const audioPromise = synthesisChain.then(function () {
                if (!isCurrent(id)) return null;
                return requestSpeechAudio(clean, signal);
            }).catch(function () {
                // A single failed sentence is skipped; the queue continues.
                return null;
            });
            synthesisChain = audioPromise.then(function () { return undefined; });
            playbackChain = playbackChain.then(function () {
                return audioPromise;
            }).then(function (audioBlob) {
                if (!audioBlob || !isCurrent(id)) return undefined;
                return play(audioBlob, id);
            }).catch(function () { return undefined; });
        }

        function idle() {
            return playbackChain.then(function () {
                return {queued: queued, played: played};
            }, function () {
                return {queued: queued, played: played};
            });
        }

        return {
            begin: begin,
            stop: stop,
            enqueue: enqueue,
            idle: idle,
            isCurrent: isCurrent
        };
    })();

    async function speakAnswer(text) {
        const sessionId = speech.begin();
        if (voiceModeActive) {
            setMicrophoneEnabled(false);
            setVoiceState("speaking", "MAE is speaking", "The microphone is paused to prevent an echo.");
        }
        const chunks = splitIntoSpeechChunks(answerForSpeech(text));
        if (!chunks.length) return {queued: 0, played: 0};
        chunks.forEach(function (chunk) {
            speech.enqueue(sessionId, chunk);
        });
        return speech.idle();
    }

    async function transcribeRecording(blob) {
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
        return String(payload.text || "").trim();
    }

    function stopListeningCycle(discard) {
        discardRecording = Boolean(discard);
        if (voiceFrame) {
            window.cancelAnimationFrame(voiceFrame);
            voiceFrame = null;
        }
        if (mediaRecorder && mediaRecorder.state === "recording") {
            mediaRecorder.stop();
        }
    }

    function monitorVoiceLevel() {
        if (
            !voiceModeActive ||
            !mediaRecorder ||
            mediaRecorder.state !== "recording" ||
            !analyser
        ) {
            return;
        }

        const samples = new Uint8Array(analyser.fftSize);
        analyser.getByteTimeDomainData(samples);
        let energy = 0;
        samples.forEach(function (sample) {
            const normalized = (sample - 128) / 128;
            energy += normalized * normalized;
        });
        const volume = Math.sqrt(energy / samples.length);
        const now = Date.now();

        if (volume >= 0.032) {
            if (!speechDetected) {
                speechDetected = true;
                speechStarted = now;
                setVoiceState(
                    "hearing",
                    "I hear you",
                    "Finish your question and pause naturally."
                );
            }
            lastSpeechAt = now;
        }

        const enoughSpeech = speechDetected && now - speechStarted >= 450;
        const naturalPause = enoughSpeech && now - lastSpeechAt >= 1050;
        const maximumUtterance = speechDetected && now - speechStarted >= 30000;
        const emptyCycleExpired = !speechDetected && now - voiceCycleStarted >= 45000;

        if (naturalPause || maximumUtterance) {
            stopListeningCycle(false);
            return;
        }
        if (emptyCycleExpired) {
            stopListeningCycle(true);
            return;
        }

        voiceFrame = window.requestAnimationFrame(monitorVoiceLevel);
    }

    function beginListeningCycle() {
        if (!voiceModeActive || maeBusy || !microphoneStream) return;

        setMicrophoneEnabled(true);
        voiceChunks = [];
        voiceCycleStarted = Date.now();
        speechStarted = 0;
        lastSpeechAt = 0;
        speechDetected = false;
        discardRecording = false;

        const options = {};
        if (MediaRecorder.isTypeSupported("audio/webm;codecs=opus")) {
            options.mimeType = "audio/webm;codecs=opus";
        }
        mediaRecorder = new MediaRecorder(microphoneStream, options);
        mediaRecorder.addEventListener("dataavailable", function (event) {
            if (event.data.size) voiceChunks.push(event.data);
        });
        mediaRecorder.addEventListener("stop", async function () {
            const shouldSubmit = voiceModeActive && speechDetected && !discardRecording;
            const mimeType = mediaRecorder.mimeType || "audio/webm";
            const recording = new Blob(voiceChunks, {type: mimeType});
            setMicrophoneEnabled(false);

            if (!shouldSubmit) {
                if (voiceModeActive && !maeBusy) {
                    window.setTimeout(beginListeningCycle, 150);
                }
                return;
            }

            setVoiceState(
                "processing",
                "Understanding your question",
                "Local speech recognition is processing the recording."
            );
            try {
                const question = await transcribeRecording(recording);
                if (!question || question.length < 2) {
                    setVoiceState(
                        "listening",
                        "I did not catch that",
                        "Please ask the question again."
                    );
                    window.setTimeout(beginListeningCycle, 500);
                    return;
                }
                await ask(question, {speakResponse: true});
            } catch (error) {
                setVoiceState(
                    "error",
                    "Voice request failed",
                    error.message || "Please try again."
                );
                if (voiceModeActive) {
                    window.setTimeout(beginListeningCycle, 1200);
                }
            }
        });
        mediaRecorder.start(250);
        setVoiceState(
            "listening",
            "Listening",
            "Ask MAE a question, then pause when you are finished."
        );
        voiceFrame = window.requestAnimationFrame(monitorVoiceLevel);
    }

    async function startVoiceMode() {
        if (!voiceReady || voiceModeActive) return;
        if (
            !navigator.mediaDevices ||
            !navigator.mediaDevices.getUserMedia ||
            typeof MediaRecorder === "undefined"
        ) {
            window.alert("This browser does not support MAE voice mode.");
            return;
        }

        voiceSession.hidden = false;
        setVoiceState(
            "processing",
            "Requesting microphone",
            "Allow microphone access when your browser asks."
        );

        try {
            microphoneStream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    echoCancellation: true,
                    noiseSuppression: true,
                    autoGainControl: true
                }
            });
            audioContext = new (window.AudioContext || window.webkitAudioContext)();
            await audioContext.resume();
            analyser = audioContext.createAnalyser();
            analyser.fftSize = 512;
            audioContext.createMediaStreamSource(microphoneStream).connect(analyser);
            voiceModeActive = true;
            voiceToggle.classList.add("is-active");
            voiceToggle.querySelector("strong").textContent = "Voice mode active";
            voiceToggle.querySelector("small").textContent = "MAE is ready to converse";
            await speakAnswer("Voice mode is ready. What would you like to know?");
            if (voiceModeActive) beginListeningCycle();
        } catch (error) {
            endVoiceMode();
            window.alert(
                error.message || "Microphone permission is required for voice mode."
            );
        }
    }

    function endVoiceMode() {
        voiceModeActive = false;
        stopListeningCycle(true);
        speech.stop();
        if (microphoneStream) {
            microphoneStream.getTracks().forEach(function (track) {
                track.stop();
            });
            microphoneStream = null;
        }
        if (audioContext) {
            audioContext.close().catch(function () {});
            audioContext = null;
        }
        analyser = null;
        voiceSession.hidden = true;
        voiceToggle.classList.remove("is-active");
        voiceToggle.querySelector("strong").textContent = "Start voice mode";
        voiceToggle.querySelector("small").textContent = "Talk naturally with MAE";
    }

    function mergeEntities(newEntities) {
        if (!newEntities || typeof newEntities !== "object") return;
        Object.keys(entities).forEach(function (key) {
            const incoming = Array.isArray(newEntities[key])
                ? newEntities[key]
                : [];
            incoming.forEach(function (value) {
                if (value && !entities[key].includes(value)) {
                    entities[key].push(value);
                }
            });
            entities[key] = entities[key].slice(-10);
        });
    }

    function buildEvidence(evidence) {
        if (!Array.isArray(evidence) || !evidence.length) return null;

        const details = document.createElement("details");
        details.className = "mae-evidence";

        const summary = document.createElement("summary");
        summary.innerHTML = `<i class="bi bi-shield-check"></i> View evidence (${evidence.length})`;
        details.appendChild(summary);

        const content = document.createElement("div");
        content.className = "mae-evidence-content";

        evidence.forEach(function (group) {
            const section = document.createElement("section");
            section.className = "mae-evidence-group";

            const heading = document.createElement("div");
            heading.className = "mae-evidence-heading";
            heading.textContent = group.source || "Read-only source";
            section.appendChild(heading);

            const metadata = document.createElement("div");
            metadata.className = "mae-evidence-metadata";
            metadata.textContent = [
                group.kind,
                group.detail,
                group.timestamp
            ].filter(Boolean).join(" · ");
            section.appendChild(metadata);

            (group.items || []).forEach(function (item) {
                const row = document.createElement("div");
                row.className = "mae-evidence-row";

                const label = document.createElement("strong");
                label.textContent = item.label || "Evidence";
                const value = document.createElement("span");
                value.textContent = item.text || "";

                row.append(label, value);
                section.appendChild(row);
            });
            content.appendChild(section);
        });

        details.appendChild(content);
        return details;
    }

    function buildChoices(choices) {
        if (!Array.isArray(choices) || !choices.length) return null;

        const choiceList = document.createElement("div");
        choiceList.className = "mae-choices";
        choices.forEach(function (choice) {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "mae-choice-button";
            button.textContent = choice.label || choice.cfs_number || "Select";
            button.addEventListener("click", function () {
                ask(choice.value || choice.cfs_number || "");
            });
            choiceList.appendChild(button);
        });
        return choiceList;
    }

    function buildAssurance(assurance) {
        if (!assurance || typeof assurance !== "object") return null;

        const panel = document.createElement("div");
        const confidence = assurance.confidence || "limited";
        panel.className = `mae-assurance mae-assurance-${confidence}`;

        const heading = document.createElement("strong");
        heading.innerHTML = '<i class="bi bi-shield-check"></i> Answer assurance';

        const confidenceChip = document.createElement("span");
        confidenceChip.className = "mae-assurance-level";
        confidenceChip.textContent = confidence.toUpperCase();

        const details = document.createElement("small");
        details.textContent = [
            assurance.authority,
            assurance.freshness,
            assurance.reason
        ].filter(Boolean).join(" · ");

        panel.append(heading, confidenceChip, details);
        return panel;
    }

    function buildTiming(timing) {
        if (!timing || typeof timing !== "object") return null;
        const totalMs = Number(timing.total_ms || 0);
        if (!totalMs) return null;
        const line = document.createElement("div");
        line.className = "mae-timing";
        line.innerHTML = `<i class="bi bi-stopwatch"></i> ${(totalMs / 1000).toFixed(1)}s total · ${Number(timing.retrieval_ms || 0)}ms research · ${Number(timing.generation_ms || 0)}ms generation`;
        return line;
    }

    async function sendFeedback(interactionId, rating, controls) {
        controls.querySelectorAll("button").forEach(function (button) {
            button.disabled = true;
        });
        const status = controls.querySelector(".mae-feedback-status");
        if (status) status.textContent = "Saving…";

        try {
            const response = await fetch("/api/mae/feedback", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                cache: "no-store",
                body: JSON.stringify({
                    interaction_id: interactionId,
                    rating: rating,
                    comment: ""
                })
            });
            const payload = await response.json();
            if (!response.ok) {
                throw new Error(payload.detail || "Feedback could not be saved.");
            }
            controls.classList.add("is-saved");
            if (status) status.textContent = "Feedback recorded";
        } catch (error) {
            controls.querySelectorAll("button").forEach(function (button) {
                button.disabled = false;
            });
            if (status) status.textContent = error.message || "Unable to save";
        }
    }

    function buildFeedback(interactionId) {
        if (!interactionId) return null;

        const controls = document.createElement("div");
        controls.className = "mae-feedback";

        const label = document.createElement("span");
        label.textContent = "Was this answer useful?";
        controls.appendChild(label);

        [
            ["helpful", "Helpful", "bi-hand-thumbs-up"],
            ["incorrect", "Incorrect", "bi-x-octagon"],
            ["incomplete", "Incomplete", "bi-exclamation-circle"],
            ["wrong_source", "Wrong source", "bi-signpost-split"]
        ].forEach(function (option) {
            const button = document.createElement("button");
            button.type = "button";
            button.dataset.rating = option[0];
            button.innerHTML = `<i class="bi ${option[2]}"></i> ${option[1]}`;
            button.addEventListener("click", function () {
                sendFeedback(interactionId, option[0], controls);
            });
            controls.appendChild(button);
        });

        const status = document.createElement("small");
        status.className = "mae-feedback-status";
        controls.appendChild(status);
        return controls;
    }

    function buildAnalyticsVisualization(spec) {
        if (!spec || !Array.isArray(spec.points) || !spec.points.length) return null;
        const panel = document.createElement("section");
        panel.className = "mae-analytics-visual";
        const heading = document.createElement("div");
        heading.className = "mae-analytics-visual-heading";
        heading.innerHTML = `<strong>${spec.title}</strong><small>${spec.period_label} Â· aggregate completed calls</small>`;
        const chartWrap = document.createElement("div");
        chartWrap.className = "mae-analytics-chart-wrap";
        const canvas = document.createElement("canvas");
        chartWrap.appendChild(canvas);
        const actions = document.createElement("div");
        actions.className = "mae-analytics-actions";

        const saveButton = document.createElement("button");
        saveButton.type = "button";
        saveButton.className = "mae-read-aloud";
        saveButton.innerHTML = '<i class="bi bi-pin-angle"></i> Save to Analytics';
        saveButton.addEventListener("click", async function () {
            saveButton.disabled = true;
            try {
                const response = await fetch("/api/analytics/widgets", {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    cache: "no-store",
                    body: JSON.stringify({title: spec.title, view_key: spec.view_key})
                });
                const payload = await response.json().catch(function () { return {}; });
                if (!response.ok) throw new Error(payload.detail || "Widget could not be saved.");
                saveButton.innerHTML = '<i class="bi bi-check-circle"></i> Saved to Analytics';
            } catch (error) {
                saveButton.disabled = false;
                window.alert(error.message || "Widget could not be saved.");
            }
        });
        actions.appendChild(saveButton);
        panel.append(heading, chartWrap, actions);

        window.setTimeout(function () {
            if (typeof Chart === "undefined") return;
            new Chart(canvas, {
                type: spec.chart_type || "bar",
                data: {
                    labels: spec.points.map(function (point) { return point.label; }),
                    datasets: [{
                        label: "Calls",
                        data: spec.points.map(function (point) { return point.value; }),
                        backgroundColor: spec.chart_type === "doughnut"
                            ? ["#4cc9ff", "#69ffb9", "#ffd66b", "#a78bfa", "#ff7b9c", "#5eead4"]
                            : "rgba(76, 201, 255, .55)",
                        borderColor: "#4cc9ff",
                        borderWidth: 1,
                        tension: .3,
                        fill: spec.chart_type === "line"
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {legend: {display: spec.chart_type === "doughnut"}},
                    scales: spec.chart_type === "doughnut" ? {} : {
                        x: {ticks: {color: "#8fb7d9"}, grid: {color: "rgba(143,183,217,.08)"}},
                        y: {beginAtZero: true, ticks: {color: "#8fb7d9", precision: 0}, grid: {color: "rgba(143,183,217,.1)"}}
                    }
                }
            });
        }, 0);
        return panel;
    }

    function addMessage(role, content, payload) {
        const responsePayload = payload || {};
        const article = document.createElement("article");
        article.className = `mae-message mae-message-${role}`;

        const avatar = document.createElement("div");
        avatar.className = role === "assistant"
            ? "mae-avatar mae-avatar-assistant"
            : "mae-avatar";
        if (role === "assistant") {
            const image = document.createElement("img");
            image.src = maeAvatarSource;
            image.alt = "";
            avatar.appendChild(image);
        } else {
            const icon = document.createElement("i");
            icon.className = "bi bi-person-fill";
            avatar.appendChild(icon);
        }

        const bubble = document.createElement("div");
        bubble.className = "mae-bubble";

        const name = document.createElement("div");
        name.className = "mae-message-name";
        name.textContent = role === "assistant" ? "MAE" : "SUPERVISOR";

        const text = document.createElement("p");
        text.textContent = content;
        bubble.append(name, text);

        if (role === "assistant" && cloudMode) {
            const citations = Array.isArray(responsePayload.citations)
                ? responsePayload.citations
                : [];
            if (citations.length) {
                const citationBlock = document.createElement("div");
                citationBlock.className = "mae-sources mae-cloud-citations";
                citationBlock.setAttribute("aria-label", "Approved document citations");
                const heading = document.createElement("strong");
                heading.textContent = "Sources";
                citationBlock.appendChild(heading);
                citations.forEach(function (citation) {
                    const chip = document.createElement("span");
                    chip.className = "mae-source-chip";
                    const location = citation.page
                        ? `page ${citation.page}`
                        : (citation.section ? `section ${citation.section}` : "approved source");
                    const revision = citation.revision ? ` · revision ${citation.revision}` : "";
                    chip.textContent = `${citation.title || "Approved document"} · ${location}${revision}`;
                    citationBlock.appendChild(chip);
                });
                bubble.appendChild(citationBlock);
            }
            if (responsePayload.report_preview) {
                const preview = responsePayload.report_preview;
                const panel = document.createElement("div");
                panel.className = "mae-report-preview";
                const notice = document.createElement("p");
                notice.textContent = `${preview.source || "approved source"} · ${preview.freshness || "freshness unavailable"}. ${preview.disclaimer || "Review before saving or exporting."}`;
                const save = document.createElement("button");
                save.type = "button";
                save.textContent = "Save as Template";
                save.addEventListener("click", async function () {
                    const title = window.prompt("Template name", "MAE report");
                    if (!title) return;
                    const result = await fetch("/api/cloud-ai/reports/templates", {
                        method: "POST", headers: {"Content-Type": "application/json"},
                        body: JSON.stringify({title: title, intent: preview.intent, visible_to_roles: ["supervisor"]})
                    });
                    if (!result.ok) window.alert("The report template could not be saved.");
                    else save.disabled = true;
                });
                panel.append(notice, save);
                bubble.appendChild(panel);
            }
            article.append(avatar, bubble);
            messages.appendChild(article);
            messages.scrollTop = messages.scrollHeight;
            return;
        }

        const sources = responsePayload.sources;
        if (Array.isArray(sources) && sources.length) {
            const sourceList = document.createElement("div");
            sourceList.className = "mae-sources";
            sources.forEach(function (source) {
                const chip = document.createElement("span");
                chip.className = "mae-source-chip";
                const availability = source.available === false
                    ? " · unavailable"
                    : "";
                chip.textContent = `${source.name} · ${source.detail}${availability}`;
                sourceList.appendChild(chip);
            });
            bubble.appendChild(sourceList);
        }

        const choices = buildChoices(responsePayload.choices);
        if (choices) bubble.appendChild(choices);

        const visualization = buildAnalyticsVisualization(responsePayload.analytics_visualization);
        if (visualization) bubble.appendChild(visualization);

        const assurance = buildAssurance(responsePayload.assurance);
        if (assurance) bubble.appendChild(assurance);

        const timing = buildTiming(responsePayload.timing);
        if (timing) bubble.appendChild(timing);

        const evidence = buildEvidence(responsePayload.evidence);
        if (evidence) bubble.appendChild(evidence);

        if (role === "assistant" && responsePayload.audit_saved) {
            const auditBadge = document.createElement("div");
            auditBadge.className = "mae-audit-badge";
            auditBadge.innerHTML = '<i class="bi bi-journal-check"></i> Inquiry audited';
            bubble.appendChild(auditBadge);
        }

        if (role === "assistant") {
            const feedback = buildFeedback(responsePayload.interaction_id);
            if (feedback) bubble.appendChild(feedback);

            const hasAnalyticsSource = Array.isArray(sources) && sources.some(function (source) {
                return source.name === "PostgreSQL analytics" && source.available !== false;
            });
            if (hasAnalyticsSource) {
                const reportButton = document.createElement("button");
                reportButton.type = "button";
                reportButton.className = "mae-read-aloud";
                reportButton.innerHTML = '<i class="bi bi-file-earmark-pdf"></i> Download analytics PDF';
                reportButton.addEventListener("click", async function () {
                    reportButton.disabled = true;
                    reportButton.innerHTML = '<i class="bi bi-arrow-repeat"></i> Preparing report...';
                    const periods = {
                        "Last 24 hours": "24h", "Last 7 days": "7d", "Last 30 days": "30d",
                        "Last 90 days": "90d", "Last 365 days": "365d"
                    };
                    const analyticsSource = sources.find(function (source) {
                        return source.name === "PostgreSQL analytics";
                    });
                    try {
                        const response = await fetch("/api/mae/analytics-report", {
                            method: "POST",
                            headers: {"Content-Type": "application/json"},
                            cache: "no-store",
                            body: JSON.stringify({
                                period: (responsePayload.analytics_visualization || {}).period_key || periods[analyticsSource.detail] || "30d",
                                view_key: (responsePayload.analytics_visualization || {}).view_key || ""
                            })
                        });
                        if (!response.ok) {
                            const error = await response.json().catch(function () { return {}; });
                            throw new Error(error.detail || "MAE could not prepare the analytics report.");
                        }
                        const blob = await response.blob();
                        const link = document.createElement("a");
                        link.href = URL.createObjectURL(blob);
                        link.download = "mae-analytics-report.pdf";
                        document.body.appendChild(link);
                        link.click();
                        link.remove();
                        window.setTimeout(function () { URL.revokeObjectURL(link.href); }, 1000);
                    } catch (error) {
                        window.alert(error.message || "MAE could not prepare the analytics report.");
                    } finally {
                        reportButton.disabled = false;
                        reportButton.innerHTML = '<i class="bi bi-file-earmark-pdf"></i> Download analytics PDF';
                    }
                });
                bubble.appendChild(reportButton);
            }

            const readButton = document.createElement("button");
            readButton.type = "button";
            readButton.className = "mae-read-aloud";
            readButton.innerHTML = '<i class="bi bi-volume-up-fill"></i> Listen';
            readButton.addEventListener("click", async function () {
                readButton.disabled = true;
                readButton.innerHTML = '<i class="bi bi-soundwave"></i> Speaking…';
                if (voiceModeActive) stopListeningCycle(true);
                try {
                    const outcome = await speakAnswer(content);
                    if (outcome && outcome.queued && !outcome.played) {
                        window.alert("MAE could not play this answer.");
                    }
                } catch (error) {
                    window.alert(error.message || "MAE could not play this answer.");
                } finally {
                    readButton.disabled = false;
                    readButton.innerHTML = '<i class="bi bi-volume-up-fill"></i> Listen';
                    if (voiceModeActive && !maeBusy) beginListeningCycle();
                }
            });
            bubble.appendChild(readButton);
        }

        article.append(avatar, bubble);
        messages.appendChild(article);
        messages.scrollTop = messages.scrollHeight;
    }

    function setBusy(busy) {
        maeBusy = busy;
        thinking.hidden = !busy;
        sendButton.disabled = busy;
        questionInput.disabled = busy;
        if (busy) messages.scrollTop = messages.scrollHeight;
    }

    async function fetchCompleteAnswer(question, requestHistory, signal) {
        const endpoint = cloudMode ? "/api/cloud-ai/advisory" : "/api/mae/chat";
        const requestBody = cloudMode
            ? {question: question}
            : {question: question, history: requestHistory, entities: entities};
        const response = await fetch(endpoint, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            cache: "no-store",
            signal: signal,
            body: JSON.stringify(requestBody)
        });
        const responseText = await response.text();
        let payload = {};
        try {
            payload = responseText ? JSON.parse(responseText) : {};
        } catch (parseError) {
            throw new Error("MAE's secure connection returned an invalid response. Please try the question again.");
        }
        if (!response.ok) {
            throw new Error(cloudMode
                ? "The approved citation service is not ready. No advisory answer was produced."
                : (payload.detail || "MAE could not complete the inquiry."));
        }
        if (cloudMode) return normalizeCloudPayload(payload);
        return payload;
    }

    function normalizeCloudPayload(payload) {
        const result = payload || {};
        if (result.denied === true) {
            return {
                answer: "No advisory answer was produced because approved citation support was unavailable.",
                citations: [],
                denied: true
            };
        }
        if (!result.answer || !Array.isArray(result.citations) || !result.citations.length) {
            return {
                answer: "No advisory answer was displayed because mandatory approved citations were missing.",
                citations: [],
                denied: true
            };
        }
        return result;
    }

    async function readNdjsonStream(response, consumeEvent) {
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let wireBuffer = "";
        while (true) {
            const part = await reader.read();
            wireBuffer += decoder.decode(part.value || new Uint8Array(), {stream: !part.done});
            const lines = wireBuffer.split("\n");
            wireBuffer = lines.pop() || "";
            lines.filter(Boolean).forEach(function (line) { consumeEvent(JSON.parse(line)); });
            if (part.done) break;
        }
        if (wireBuffer.trim()) consumeEvent(JSON.parse(wireBuffer));
    }

    // Cloud advisory: speech begins on the first completed sentence instead of
    // waiting for the whole generated answer.
    async function fetchCloudStreamedAnswer(question, speakResponse, signal) {
        const response = await fetch("/api/cloud-ai/advisory/stream", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            cache: "no-store",
            signal: signal,
            body: JSON.stringify({question: question, persona: "mae"})
        });
        if (response.status === 404 || response.status === 405) {
            advisoryStreamAvailable = false;
            throw new Error("The cloud advisory stream is unavailable.");
        }
        if (!response.ok || !response.body) {
            throw new Error("The cloud advisory stream is unavailable.");
        }

        const sessionId = speakResponse ? speech.begin() : 0;
        let payload = null;
        let failure = "";
        let spoken = false;

        function consumeEvent(event) {
            if (!event || typeof event !== "object") return;
            if (event.type === "chunk") {
                if (!speakResponse) return;
                const chunkText = String(event.speech || event.text || "");
                if (!chunkText.trim()) return;
                if (!spoken) {
                    spoken = true;
                    if (voiceModeActive) setMicrophoneEnabled(false);
                    setVoiceState(
                        "speaking",
                        "MAE is speaking",
                        "The rest of the answer is still being prepared."
                    );
                }
                speech.enqueue(sessionId, chunkText);
            } else if (event.type === "complete") {
                payload = event.payload || {};
            } else if (event.type === "error") {
                failure = String(event.detail || "");
            }
        }

        try {
            await readNdjsonStream(response, consumeEvent);
        } catch (error) {
            if (speakResponse) speech.stop();
            throw error;
        }
        if (failure) {
            if (speakResponse) speech.stop();
            throw new Error(failure);
        }
        if (!payload) {
            if (speakResponse) speech.stop();
            throw new Error("The cloud advisory stream ended early.");
        }
        const normalized = normalizeCloudPayload(payload);
        if (speakResponse && !spoken && normalized.answer) {
            splitIntoSpeechChunks(answerForSpeech(normalized.answer)).forEach(function (chunk) {
                speech.enqueue(sessionId, chunk);
            });
            spoken = true;
        }
        if (speakResponse) await speech.idle();
        return normalized;
    }

    async function fetchStreamedAnswer(question, requestHistory, signal) {
        const response = await fetch("/api/mae/chat/stream", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            cache: "no-store",
            signal: signal,
            body: JSON.stringify({question: question, history: requestHistory, entities: entities})
        });
        if (!response.ok || !response.body) throw new Error("MAE's response stream is unavailable.");

        const sessionId = speech.begin();
        const chunker = createSpeechChunker();
        let payload = null;
        let failure = "";
        let spoken = false;

        function queueSpeechChunks(chunks) {
            chunks.forEach(function (chunk) {
                if (!chunk || !voiceModeActive) return;
                if (!spoken) {
                    spoken = true;
                    setMicrophoneEnabled(false);
                    setVoiceState(
                        "speaking",
                        "MAE is speaking",
                        "The next part of the answer is being prepared."
                    );
                }
                speech.enqueue(sessionId, chunk);
            });
        }

        function consumeEvent(event) {
            if (!event || typeof event !== "object") return;
            if (event.type === "token") {
                queueSpeechChunks(chunker.feed(String(event.text || "")));
            } else if (event.type === "complete") {
                payload = event.payload || {};
            } else if (event.type === "error") {
                failure = String(event.detail || "MAE could not complete the inquiry.");
            }
        }

        try {
            await readNdjsonStream(response, consumeEvent);
        } catch (error) {
            speech.stop();
            throw error;
        }
        if (failure) {
            speech.stop();
            throw new Error(failure);
        }
        if (!payload) {
            speech.stop();
            throw new Error("MAE's response stream ended early.");
        }
        queueSpeechChunks(chunker.flush());
        if (!spoken && payload.answer) {
            queueSpeechChunks(splitIntoSpeechChunks(answerForSpeech(payload.answer)));
        }
        await speech.idle();
        return payload;
    }

    async function ask(question, options) {
        if (!question) return;
        const settings = options || {};
        let answerToSpeak = "";
        let alreadySpoken = false;
        addMessage("user", question);
        const requestHistory = history.slice(-8);
        history.push({role: "user", content: question});
        setBusy(true);
        if (settings.speakResponse && voiceModeActive) {
            setVoiceState(
                "processing",
                "MAE is checking the information",
                "The existing read-only MAE workflow is answering your question."
            );
        }

        const controller = new AbortController();
        const timeoutId = window.setTimeout(function () {
            controller.abort();
        }, maeRequestTimeoutMs);

        try {
            let payload;
            const wantsSpeech = Boolean(settings.speakResponse && voiceModeActive);
            if (wantsSpeech) {
                try {
                    if (cloudMode) {
                        if (!advisoryStreamAvailable) {
                            throw new Error("The cloud advisory stream is unavailable.");
                        }
                        payload = await fetchCloudStreamedAnswer(
                            question, wantsSpeech, controller.signal
                        );
                    } else {
                        payload = await fetchStreamedAnswer(question, requestHistory, controller.signal);
                    }
                    alreadySpoken = wantsSpeech;
                } catch (streamError) {
                    if (streamError.name === "AbortError") throw streamError;
                    // Streaming is unavailable; fall back to the whole-answer path.
                    if (wantsSpeech) {
                        setVoiceState("processing", "MAE is completing the answer", "The standard private response path is being used.");
                    }
                    payload = await fetchCompleteAnswer(question, requestHistory, controller.signal);
                    if (wantsSpeech) {
                        await speakAnswer(payload.answer);
                        alreadySpoken = true;
                    }
                }
            } else {
                payload = await fetchCompleteAnswer(question, requestHistory, controller.signal);
            }
            if (!cloudMode) mergeEntities(payload.entities);
            addMessage("assistant", payload.answer, payload);
            history.push({role: "assistant", content: payload.answer});
            answerToSpeak = payload.answer;
        } catch (error) {
            const message = error.name === "AbortError"
                ? "The live information request took too long. Please try again."
                : (error.message || String(error));
            answerToSpeak = `I could not complete that inquiry. ${message}`;
            addMessage("assistant", answerToSpeak);
        } finally {
            window.clearTimeout(timeoutId);
            setBusy(false);
        }

        if (settings.speakResponse && voiceModeActive && answerToSpeak && !alreadySpoken) {
            try {
                await speakAnswer(answerToSpeak);
            } catch (error) {
                setVoiceState(
                    "error",
                    "I could not play the answer",
                    error.message || "The written answer is still available above."
                );
            }
        }

        if (voiceModeActive) {
            beginListeningCycle();
        } else {
            questionInput.focus();
        }
    }

    form.addEventListener("submit", function (event) {
        event.preventDefault();
        const question = questionInput.value.trim();
        if (!question) return;
        questionInput.value = "";
        // A new question abandons any queued or playing audio from the last one.
        speech.stop();
        if (voiceModeActive) stopListeningCycle(true);
        ask(question, {speakResponse: voiceModeActive});
    });

    questionInput.addEventListener("keydown", function (event) {
        if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            form.requestSubmit();
        }
    });

    document.querySelectorAll("[data-mae-prompt]").forEach(function (button) {
        button.addEventListener("click", function () {
            questionInput.value = button.dataset.maePrompt || "";
            questionInput.focus();
        });
    });

    voiceToggle.addEventListener("click", function () {
        if (voiceModeActive) {
            endVoiceMode();
        } else {
            startVoiceMode();
        }
    });
    voiceStop.addEventListener("click", endVoiceMode);
    window.addEventListener("beforeunload", endVoiceMode);

    // Cloud Polly voices are enum-bound (Joanna / Matthew); the on-prem voice
    // names are not accepted there, so the active voice comes from the server.
    async function loadCloudVoiceProfile() {
        try {
            const response = await fetch("/api/cloud-ai/status", {cache: "no-store"});
            if (!response.ok) return;
            const status = await response.json();
            const tts = status.tts || {};
            const stt = status.stt || {};
            cloudVoiceName = String(tts.voice || "");
            if (!tts.ready) sentenceSpeechAvailable = false;
            voiceReady = Boolean(status.connected && tts.ready && stt.ready);
            if (voiceToggle && voiceReady) {
                voiceToggle.disabled = false;
                voiceToggle.title = "Start a private voice conversation with MAE";
                const label = voiceToggle.querySelector("small");
                if (label) label.textContent = "Talk naturally with MAE";
            }
        } catch (error) {
            cloudVoiceName = "";
        }
    }

    if (cloudMode) {
        setStatus("mae-ai-status", true, "Citation-only advisory");
        setStatus("mae-db-status", false, "Not used in cloud advisory");
        setStatus("mae-cad-status", false, "No CAD access");
        loadCloudVoiceProfile();
    } else {
        loadStatus();
        loadVoiceStatus();
    }
})();
