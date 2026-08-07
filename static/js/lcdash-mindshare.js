(function () {
    "use strict";

    const form = document.getElementById("mindshare-form");
    const questionInput = document.getElementById("mindshare-question");
    const sendButton = document.getElementById("mindshare-send");
    const messages = document.getElementById("mindshare-messages");
    const thinking = document.getElementById("mindshare-thinking");
    const voiceToggle = document.getElementById("jack-voice-toggle");
    const voiceSession = document.getElementById("jack-voice-session");
    const voiceStop = document.getElementById("jack-voice-stop");
    const voiceState = document.getElementById("jack-voice-state");
    const voiceDetail = document.getElementById("jack-voice-detail");
    const voicePlayer = document.getElementById("jack-voice-player");
    const history = [];

    let jackBusy = false;
    let voiceReady = false;
    let voiceModeActive = false;
    let microphoneStream = null;
    let audioContext = null;
    let analyser = null;
    let mediaRecorder = null;
    let microphoneSource = null;
    // Cloud mode captures raw PCM instead of using MediaRecorder: Amazon
    // Transcribe streaming cannot ingest the webm/opus MediaRecorder produces.
    let pcmCapture = null;
    let voiceChunks = [];
    let voiceFrame = null;
    let voiceCycleStarted = 0;
    let speechStarted = 0;
    let lastSpeechAt = 0;
    let speechDetected = false;
    let discardRecording = false;
    let cloudMode = false;
    let cloudVoiceName = "";
    let sentenceSpeechAvailable = true;
    let advisoryStreamAvailable = true;

    if (!form || !questionInput || !messages) return;

    const approvedSource = document.querySelector(".mindshare-assistant")?.dataset.approvedSource === "true";
    if (!approvedSource) {
        questionInput.disabled = true;
        questionInput.placeholder = "Approved Mindshare source unavailable";
        sendButton.disabled = true;
        if (voiceToggle) voiceToggle.disabled = true;
        document.querySelectorAll("[data-mindshare-prompt]").forEach(function (button) {
            button.disabled = true;
        });
        ["mindshare-ai-status", "mindshare-library-status"].forEach(function (cardId) {
            const card = document.getElementById(cardId);
            if (!card) return;
            card.classList.add("is-offline");
            const value = card.querySelector("strong");
            if (value) value.textContent = "Unavailable";
        });
        return;
    }

    function updateStatusCard(id, online, text) {
        const card = document.getElementById(id);
        if (!card) return;
        card.classList.toggle("is-online", Boolean(online));
        card.classList.toggle("is-offline", !online);
        const value = card.querySelector("strong");
        if (value) value.textContent = text;
    }

    async function loadStatus() {
        try {
            const response = await fetch("/api/mindshare/status", {
                cache: "no-store"
            });
            const payload = await response.json();
            updateStatusCard(
                "mindshare-ai-status",
                Boolean(payload.assistant && payload.assistant.connected),
                payload.assistant && payload.assistant.connected
                    ? payload.assistant.model
                    : "Unavailable"
            );
            const knowledge = payload.knowledge || {};
            updateStatusCard(
                "mindshare-library-status",
                Boolean(knowledge.connected && knowledge.documents),
                knowledge.connected
                    ? `${knowledge.documents || 0} documents`
                    : "Unavailable"
            );
        } catch (error) {
            updateStatusCard("mindshare-ai-status", false, "Unavailable");
            updateStatusCard("mindshare-library-status", false, "Unavailable");
        }
    }

    async function loadVoiceStatus() {
        if (!voiceToggle) return;
        try {
            const response = await fetch("/api/voice/status", {
                cache: "no-store"
            });
            const status = await response.json();
            cloudMode = Boolean(status.cloud_mode);
            const tts = status.tts || {};
            const stt = status.stt || {};
            if (cloudMode) {
                // Cloud Polly voices are enum-bound; the on-prem voice names
                // are rejected there, so take the active voice from the server.
                cloudVoiceName = String(tts.voice || "");
                if (!tts.ready) sentenceSpeechAvailable = false;
                voiceReady = Boolean(
                    response.ok && status.connected && tts.ready && stt.ready
                );
            } else {
                voiceReady = Boolean(
                    response.ok &&
                    status.connected &&
                    tts.ready &&
                    status.jack_tts &&
                    status.jack_tts.ready &&
                    stt.ready
                );
            }
        } catch (error) {
            voiceReady = false;
        }

        voiceToggle.disabled = !voiceReady;
        voiceToggle.title = voiceReady
            ? "Start a private voice conversation with JACK"
            : "The local speech models are not ready";
        if (!voiceReady) {
            voiceToggle.querySelector("small").textContent =
                "Voice service unavailable";
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
    // Sources are rendered separately and are never part of spoken audio.
    // Every chunk is sanitized here and again on the server before Polly.
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
    // are grouped so the cadence stays natural.
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
                    persona: "jack",
                    voice: cloudVoiceName || ""
                })
            });
            if (sentenceResponse.ok) return sentenceResponse.blob();
            if (sentenceResponse.status === 404 || sentenceResponse.status === 405) {
                sentenceSpeechAvailable = false;
            } else {
                const detail = await sentenceResponse.json().catch(function () {
                    return {};
                });
                throw new Error(detail.detail || "JACK could not generate speech.");
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
                    ? (cloudVoiceName || "Matthew")
                    : "jack-synthetic-southern-male",
                speed: cloudMode ? 1.0 : 0.92,
                response_format: "mp3"
            })
        });
        if (!response.ok) {
            const payload = await response.json().catch(function () {
                return {};
            });
            throw new Error(
                payload.detail || "JACK could not generate speech."
            );
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
            setVoiceState(
                "speaking",
                "JACK is speaking",
                "The microphone is paused to prevent an echo."
            );
        }
        const chunks = splitIntoSpeechChunks(answerForSpeech(text));
        if (!chunks.length) return {queued: 0, played: 0};
        chunks.forEach(function (chunk) {
            speech.enqueue(sessionId, chunk);
        });
        return speech.idle();
    }

    async function transcribeRecording(blob, clip) {
        const formData = new FormData();
        if (clip && clip.audioFormat === "pcm") {
            // The server defaults to webm-opus/48000; raw PCM must declare its
            // own format and rate or the push-to-talk contract rejects it.
            formData.append("file", blob, "jack-question.pcm");
            formData.append("audio_format", clip.audioFormat);
            formData.append("sample_rate_hz", String(clip.sampleRateHz));
            formData.append(
                "duration_seconds",
                String(Math.min(30, Math.max(0.1, clip.durationSeconds)))
            );
        } else {
            formData.append("file", blob, "jack-question.webm");
        }
        const response = await fetch("/api/voice/transcribe", {
            method: "POST",
            cache: "no-store",
            body: formData
        });
        const payload = await response.json();
        if (!response.ok) {
            throw new Error(
                payload.detail || "JACK could not transcribe the question."
            );
        }
        return String(payload.text || "").trim();
    }

    // Shared tail for both capture paths so cloud and on-prem behave
    // identically once a clip exists.
    async function finishVoiceRecording(recording, clip) {
        const shouldSubmit =
            voiceModeActive && speechDetected && !discardRecording && Boolean(recording);
        setMicrophoneEnabled(false);

        if (!shouldSubmit) {
            if (voiceModeActive && !jackBusy) {
                window.setTimeout(beginListeningCycle, 150);
            }
            return;
        }

        setVoiceState(
            "processing",
            "Understanding your question",
            cloudMode
                ? "Amazon Transcribe is processing the recording."
                : "Local speech recognition is processing the recording."
        );
        try {
            const question = await transcribeRecording(recording, clip);
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
    }

    function stopListeningCycle(discard) {
        discardRecording = Boolean(discard);
        if (voiceFrame) {
            window.cancelAnimationFrame(voiceFrame);
            voiceFrame = null;
        }
        if (pcmCapture) {
            const capture = pcmCapture;
            pcmCapture = null;
            capture.stop().then(function (clip) {
                finishVoiceRecording(clip ? clip.blob : null, clip);
            }).catch(function () {
                finishVoiceRecording(null, null);
            });
            return;
        }
        if (mediaRecorder && mediaRecorder.state === "recording") {
            mediaRecorder.stop();
        }
    }

    function isCapturing() {
        if (cloudMode) return Boolean(pcmCapture);
        return Boolean(mediaRecorder) && mediaRecorder.state === "recording";
    }

    function monitorVoiceLevel() {
        // The cloud path has no MediaRecorder, so capture liveness must be
        // asked of whichever recorder is actually running. Getting this wrong
        // stops the natural-pause detector on its first frame and the clip
        // never ends.
        if (!voiceModeActive || !isCapturing() || !analyser) {
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
        const maximumUtterance =
            speechDetected && now - speechStarted >= 30000;
        const emptyCycleExpired =
            !speechDetected && now - voiceCycleStarted >= 45000;

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
        if (!voiceModeActive || jackBusy || !microphoneStream) return;

        setMicrophoneEnabled(true);
        voiceChunks = [];
        voiceCycleStarted = Date.now();
        speechStarted = 0;
        lastSpeechAt = 0;
        speechDetected = false;
        discardRecording = false;

        if (cloudMode) {
            if (!window.LCDashVoiceCapture || !microphoneSource) {
                setVoiceState(
                    "error",
                    "Voice capture unavailable",
                    "This browser cannot capture audio for cloud transcription."
                );
                return;
            }
            try {
                pcmCapture = window.LCDashVoiceCapture.start(
                    audioContext, microphoneSource
                );
            } catch (error) {
                pcmCapture = null;
                setVoiceState(
                    "error",
                    "Voice capture unavailable",
                    error.message || "Cloud audio capture could not start."
                );
                return;
            }
        } else {
            const options = {};
            if (MediaRecorder.isTypeSupported("audio/webm;codecs=opus")) {
                options.mimeType = "audio/webm;codecs=opus";
            }
            mediaRecorder = new MediaRecorder(microphoneStream, options);
            mediaRecorder.addEventListener("dataavailable", function (event) {
                if (event.data.size) voiceChunks.push(event.data);
            });
            mediaRecorder.addEventListener("stop", function () {
                const mimeType = mediaRecorder.mimeType || "audio/webm";
                finishVoiceRecording(new Blob(voiceChunks, {type: mimeType}), null);
            });
            mediaRecorder.start(250);
        }
        setVoiceState(
            "listening",
            "Listening",
            "Ask JACK a Mindshare question, then pause when you are finished."
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
            window.alert("This browser does not support JACK voice mode.");
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
            audioContext = new (
                window.AudioContext || window.webkitAudioContext
            )();
            await audioContext.resume();
            analyser = audioContext.createAnalyser();
            analyser.fftSize = 512;
            // Retained so cloud PCM capture can tap the same source node
            // rather than opening a second one on the same stream.
            microphoneSource = audioContext.createMediaStreamSource(
                microphoneStream
            );
            microphoneSource.connect(analyser);
            voiceModeActive = true;
            voiceToggle.classList.add("is-active");
            voiceToggle.querySelector("strong").textContent =
                "Voice mode active";
            voiceToggle.querySelector("small").textContent =
                "JACK is ready to converse";
            await speakAnswer(
                "JACK voice mode is ready. What Mindshare question can I help you solve?"
            );
            if (voiceModeActive) beginListeningCycle();
        } catch (error) {
            endVoiceMode();
            window.alert(
                error.message ||
                "Microphone permission is required for voice mode."
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
        microphoneSource = null;
        pcmCapture = null;
        if (voiceSession) voiceSession.hidden = true;
        if (voiceToggle) {
            voiceToggle.classList.remove("is-active");
            voiceToggle.querySelector("strong").textContent =
                "Start voice mode";
            voiceToggle.querySelector("small").textContent =
                "Talk naturally with JACK";
        }
    }

    function addEvidence(bubble, evidence) {
        if (!Array.isArray(evidence) || !evidence.length) return;
        const details = document.createElement("details");
        details.className = "mae-evidence";
        const summary = document.createElement("summary");
        summary.textContent = `Supporting documents (${evidence.length})`;
        const content = document.createElement("div");
        content.className = "mae-evidence-content";

        evidence.forEach(function (item) {
            const group = document.createElement("div");
            group.className = "mae-evidence-group";
            const heading = document.createElement(
                item.document_id ? "a" : "div"
            );
            heading.className = "mae-evidence-heading";
            heading.textContent =
                item.title || item.file_name || "Mindshare document";
            if (item.document_id) {
                heading.href =
                    `/knowledge/documents/mindshare/${item.document_id}`;
                heading.target = "_blank";
                heading.rel = "noopener noreferrer";
                heading.title = "Open the supporting PDF";
            }
            const metadata = document.createElement("div");
            metadata.className = "mae-evidence-metadata";
            metadata.textContent = item.page_number
                ? `Page ${item.page_number}`
                : "Page not reported";
            const passage = document.createElement("p");
            passage.textContent = item.content || "";
            group.append(heading, metadata, passage);
            content.appendChild(group);
        });
        details.append(summary, content);
        bubble.appendChild(details);
    }

    async function sendFeedback(interactionId, rating, controls) {
        controls.querySelectorAll("button").forEach(function (button) {
            button.disabled = true;
        });
        const status = controls.querySelector(".mae-feedback-status");
        if (status) status.textContent = "Saving…";
        try {
            const response = await fetch("/api/mindshare/feedback", {
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

    // Renders one answer into a standalone print window rather than printing
    // the surrounding dashboard. Everything is written as text nodes, never
    // interpolated HTML, so answer or citation content cannot inject markup.
    function printAnswer(answerText, citations, questionText) {
        const frame = document.createElement("iframe");
        frame.setAttribute("aria-hidden", "true");
        frame.style.position = "fixed";
        frame.style.right = "0";
        frame.style.bottom = "0";
        frame.style.width = "0";
        frame.style.height = "0";
        frame.style.border = "0";
        document.body.appendChild(frame);

        const frameDoc = frame.contentDocument;
        const style = frameDoc.createElement("style");
        style.textContent = [
            "body{font-family:Georgia,'Times New Roman',serif;color:#111;margin:32px;line-height:1.5}",
            "h1{font-size:18pt;margin:0 0 2px}",
            ".meta{font-size:9pt;color:#555;margin-bottom:18px;border-bottom:1px solid #bbb;padding-bottom:10px}",
            ".label{font-size:9pt;letter-spacing:.08em;text-transform:uppercase;color:#555;margin:18px 0 4px}",
            ".question{font-size:11pt;font-style:italic;color:#333}",
            ".answer{font-size:12pt;white-space:pre-wrap}",
            "ul{margin:4px 0 0;padding-left:20px}",
            "li{font-size:9.5pt;color:#333;margin-bottom:3px}",
            ".note{margin-top:24px;padding-top:10px;border-top:1px solid #bbb;font-size:8.5pt;color:#555}"
        ].join("");
        frameDoc.head.appendChild(style);
        frameDoc.title = "JACK answer";

        function block(className, tag, textContent) {
            const node = frameDoc.createElement(tag);
            node.className = className;
            node.textContent = textContent;
            frameDoc.body.appendChild(node);
            return node;
        }

        block("", "h1", "Logan County 911 — JACK");
        block("meta", "div", `Mindshare technical assistant · printed ${new Date().toLocaleString()}`);
        if (questionText) {
            block("label", "div", "Question");
            block("question", "div", questionText);
        }
        block("label", "div", "Answer");
        block("answer", "div", answerText);

        if (citations && citations.length) {
            block("label", "div", "Sources");
            const list = frameDoc.createElement("ul");
            citations.forEach(function (citation) {
                const label = citation.title || "Approved document";
                const item = frameDoc.createElement("li");
                item.textContent = citation.page ? `${label}, page ${citation.page}` : label;
                list.appendChild(item);
            });
            frameDoc.body.appendChild(list);
        }

        block(
            "note",
            "div",
            "Advisory only. JACK cannot write to CAD, control station tones, or "
            + "operate ESInet services. Verify critical information at the source."
        );

        frame.contentWindow.focus();
        frame.contentWindow.print();
        // Safari/Firefox return from print() before the dialog closes, so the
        // frame is removed on a timer rather than immediately.
        window.setTimeout(function () { frame.remove(); }, 1000);
    }

    function addMessage(role, content, payload) {
        const article = document.createElement("article");
        article.className = `mae-message mae-message-${role}`;
        const avatar = document.createElement("div");
        avatar.className = "mae-avatar";
        avatar.innerHTML = role === "assistant"
            ? '<i class="bi bi-broadcast-pin"></i>'
            : '<i class="bi bi-person-fill"></i>';
        const bubble = document.createElement("div");
        bubble.className = "mae-bubble";
        const name = document.createElement("div");
        name.className = "mae-message-name";
        name.textContent = role === "assistant" ? "JACK" : "USER";
        const text = document.createElement("p");
        text.textContent = content;
        bubble.append(name, text);

        if (payload && payload.assurance) {
            const assurance = document.createElement("div");
            assurance.className =
                `mae-assurance mae-assurance-${payload.assurance.level || "supported"}`;
            assurance.innerHTML = `
                <strong>${payload.assurance.label || "Documentation supported"}</strong>
                <span class="mae-assurance-level">${(payload.assurance.level || "supported").toUpperCase()}</span>
                <small>${payload.assurance.detail || ""}</small>
            `;
            bubble.appendChild(assurance);
        }
        addEvidence(bubble, payload && payload.evidence);

        if (role === "assistant" && payload && payload.report_preview) {
            const preview = payload.report_preview;
            const panel = document.createElement("div");
            panel.className = "mae-report-preview";
            const notice = document.createElement("p");
            notice.textContent = `${preview.source || "approved source"} · ${preview.freshness || "freshness unavailable"}. ${preview.disclaimer || "Review before saving or exporting."}`;
            const save = document.createElement("button");
            save.type = "button";
            save.textContent = "Save as Template";
            save.addEventListener("click", async function () {
                const title = window.prompt("Template name", "JACK report");
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

        const citations = payload && Array.isArray(payload.citations)
            ? payload.citations
            : [];
        if (role === "assistant" && citations.length) {
            const sources = document.createElement("div");
            sources.className = "mae-sources mae-cloud-citations";
            sources.setAttribute("aria-label", "Approved document sources");
            const heading = document.createElement("strong");
            heading.textContent = "Sources";
            sources.appendChild(heading);
            const list = document.createElement("ul");
            citations.forEach(function (citation) {
                const item = document.createElement("li");
                const label = citation.title || "Approved document";
                const detail = citation.page ? `${label}, page ${citation.page}` : label;
                item.textContent = detail;
                list.appendChild(item);
            });
            sources.appendChild(list);
            bubble.appendChild(sources);
        }

        if (role === "assistant") {
            const feedback = buildFeedback(payload && payload.interaction_id);
            if (feedback) bubble.appendChild(feedback);

            if (content) {
                const printButton = document.createElement("button");
                printButton.type = "button";
                printButton.className = "mae-read-aloud";
                printButton.innerHTML = '<i class="bi bi-printer"></i> Print answer';
                printButton.addEventListener("click", function () {
                    printAnswer(content, citations, payload && payload.question);
                });
                bubble.appendChild(printButton);
            }

            const readButton = document.createElement("button");
            readButton.type = "button";
            readButton.className = "mae-read-aloud";
            readButton.innerHTML =
                '<i class="bi bi-volume-up-fill"></i> Listen';
            readButton.addEventListener("click", async function () {
                readButton.disabled = true;
                readButton.innerHTML =
                    '<i class="bi bi-soundwave"></i> Speaking…';
                if (voiceModeActive) stopListeningCycle(true);
                try {
                    const outcome = await speakAnswer(content);
                    if (outcome && outcome.queued && !outcome.played) {
                        window.alert("JACK could not play this answer.");
                    }
                } catch (error) {
                    window.alert(
                        error.message || "JACK could not play this answer."
                    );
                } finally {
                    readButton.disabled = false;
                    readButton.innerHTML =
                        '<i class="bi bi-volume-up-fill"></i> Listen';
                    if (voiceModeActive && !jackBusy) beginListeningCycle();
                }
            });
            bubble.appendChild(readButton);
        }

        article.append(avatar, bubble);
        messages.appendChild(article);
        messages.scrollTop = messages.scrollHeight;
    }

    function setBusy(busy) {
        jackBusy = busy;
        thinking.hidden = !busy;
        sendButton.disabled = busy;
        questionInput.disabled = busy;
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
            lines.filter(Boolean).forEach(function (line) {
                consumeEvent(JSON.parse(line));
            });
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
            body: JSON.stringify({question: question, persona: "jack"})
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
                        "JACK is speaking",
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
            throw new Error("JACK's advisory stream ended early.");
        }
        if (speakResponse && !spoken && payload.answer && !payload.denied) {
            splitIntoSpeechChunks(answerForSpeech(payload.answer)).forEach(function (chunk) {
                speech.enqueue(sessionId, chunk);
            });
            spoken = true;
        }
        if (speakResponse) await speech.idle();
        return payload;
    }

    async function fetchStreamedAnswer(question, requestHistory, speakResponse, signal) {
        if (cloudMode) {
            return fetchCloudStreamedAnswer(question, speakResponse, signal);
        }
        const response = await fetch("/api/mindshare/chat/stream", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            cache: "no-store",
            signal: signal,
            body: JSON.stringify({question: question, history: requestHistory})
        });
        if (!response.ok || !response.body) {
            throw new Error("JACK's response stream is unavailable.");
        }

        const sessionId = speakResponse ? speech.begin() : 0;
        const chunker = createSpeechChunker();
        let payload = null;
        let failure = "";
        let spoken = false;

        function queueSpeechChunks(chunks) {
            chunks.forEach(function (chunk) {
                if (!chunk || !speakResponse || !voiceModeActive) return;
                if (!spoken) {
                    spoken = true;
                    setMicrophoneEnabled(false);
                    setVoiceState(
                        "speaking",
                        "JACK is speaking",
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
                failure = String(event.detail || "JACK could not complete the inquiry.");
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
            throw new Error("JACK's response stream ended early.");
        }
        queueSpeechChunks(chunker.flush());
        if (speakResponse && !spoken && payload.answer) {
            queueSpeechChunks(splitIntoSpeechChunks(answerForSpeech(payload.answer)));
        }
        if (speakResponse) await speech.idle();
        return payload;
    }

    async function fetchCompleteAnswer(question, requestHistory, signal) {
        const response = await fetch("/api/mindshare/chat", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            cache: "no-store",
            signal: signal,
            body: JSON.stringify({question: question, history: requestHistory})
        });
        const payload = await response.json();
        if (!response.ok) {
            throw new Error(payload.detail || "The inquiry could not be completed.");
        }
        return payload;
    }

    async function ask(question, options) {
        if (!question) return;
        const settings = options || {};
        let answerToSpeak = "";
        const requestHistory = history.slice(-2);
        addMessage("user", question);
        history.push({role: "user", content: question});
        setBusy(true);

        if (settings.speakResponse && voiceModeActive) {
            setVoiceState(
                "processing",
                "JACK is checking the library",
                "The existing read-only Mindshare workflow is answering your question."
            );
        }

        const controller = new AbortController();
        const timeoutId = window.setTimeout(function () {
            controller.abort();
        }, 120000);
        const progressId = window.setTimeout(function () {
            if (settings.speakResponse && voiceModeActive && jackBusy) {
                setVoiceState(
                    "processing",
                    "JACK is preparing the answer",
                    "The relevant manuals are loaded. Local AI is composing a concise response."
                );
            }
        }, 15000);

        try {
            let payload;
            const wantsSpeech = Boolean(settings.speakResponse && voiceModeActive);
            if (wantsSpeech && (!cloudMode || advisoryStreamAvailable)) {
                try {
                    payload = await fetchStreamedAnswer(
                        question, requestHistory, true, controller.signal
                    );
                } catch (streamError) {
                    if (streamError.name === "AbortError") throw streamError;
                    // Streaming is unavailable; fall back to the whole-answer path.
                    setVoiceState(
                        "processing",
                        "JACK is completing the answer",
                        "The standard private response path is being used."
                    );
                    payload = await fetchCompleteAnswer(
                        question, requestHistory, controller.signal
                    );
                    await speakAnswer(payload.answer);
                }
            } else {
                payload = await fetchCompleteAnswer(
                    question, requestHistory, controller.signal
                );
            }
            addMessage("assistant", payload.answer, {...payload, question: question});
            history.push({role: "assistant", content: payload.answer});
            answerToSpeak = payload.answer;
        } catch (error) {
            const message = error.name === "AbortError"
                ? "The Mindshare inquiry took too long. Please try again."
                : (error.message || String(error));
            answerToSpeak =
                `I could not complete that inquiry. ${message}`;
            addMessage("assistant", answerToSpeak);
        } finally {
            window.clearTimeout(timeoutId);
            window.clearTimeout(progressId);
            setBusy(false);
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

    document.querySelectorAll("[data-mindshare-prompt]").forEach(function (button) {
        button.addEventListener("click", function () {
            questionInput.value = button.dataset.mindsharePrompt || "";
            questionInput.focus();
        });
    });

    if (voiceToggle) {
        voiceToggle.addEventListener("click", function () {
            if (voiceModeActive) {
                endVoiceMode();
            } else {
                startVoiceMode();
            }
        });
    }
    if (voiceStop) voiceStop.addEventListener("click", endVoiceMode);
    window.addEventListener("beforeunload", endVoiceMode);

    loadStatus();
    loadVoiceStatus();
})();
