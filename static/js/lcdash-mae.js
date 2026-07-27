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
    let activeAudioUrl = "";

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
                status.centralsquare.configured,
                status.centralsquare.configured
                    ? "Read-only ready"
                    : "Not configured"
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
            : "The local speech models are not ready";
        if (!voiceReady) {
            voiceToggle.querySelector("small").textContent = "Voice service unavailable";
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

    async function speakAnswer(text) {
        const spokenText = String(text || "").trim().slice(0, 2500);
        if (!spokenText) return;

        if (voiceModeActive) {
            setMicrophoneEnabled(false);
            setVoiceState(
                "speaking",
                "MAE is speaking",
                "The microphone is paused to prevent an echo."
            );
        }

        const response = await fetch("/api/voice/speech", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            cache: "no-store",
            body: JSON.stringify({
                text: spokenText,
                voice: "af_heart",
                speed: 1.0,
                response_format: "mp3"
            })
        });
        if (!response.ok) {
            const payload = await response.json().catch(function () { return {}; });
            throw new Error(payload.detail || "MAE could not generate speech.");
        }

        const audioBlob = await response.blob();
        if (activeAudioUrl) URL.revokeObjectURL(activeAudioUrl);
        activeAudioUrl = URL.createObjectURL(audioBlob);
        voicePlayer.src = activeAudioUrl;

        await new Promise(function (resolve, reject) {
            voicePlayer.onended = resolve;
            voicePlayer.onerror = function () {
                reject(new Error("The browser could not play MAE's voice."));
            };
            const playPromise = voicePlayer.play();
            if (playPromise) playPromise.catch(reject);
        });
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
        if (voicePlayer) {
            voicePlayer.pause();
            voicePlayer.removeAttribute("src");
        }
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

            const readButton = document.createElement("button");
            readButton.type = "button";
            readButton.className = "mae-read-aloud";
            readButton.innerHTML = '<i class="bi bi-volume-up-fill"></i> Listen';
            readButton.addEventListener("click", async function () {
                readButton.disabled = true;
                readButton.innerHTML = '<i class="bi bi-soundwave"></i> Speaking…';
                if (voiceModeActive) stopListeningCycle(true);
                try {
                    await speakAnswer(content);
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

    async function ask(question, options) {
        if (!question) return;
        const settings = options || {};
        let answerToSpeak = "";
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
        }, 30000);

        try {
            const response = await fetch("/api/mae/chat", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                cache: "no-store",
                signal: controller.signal,
                body: JSON.stringify({
                    question: question,
                    history: requestHistory,
                    entities: entities
                })
            });
            const responseText = await response.text();
            let payload = {};

            try {
                payload = responseText ? JSON.parse(responseText) : {};
            } catch (parseError) {
                throw new Error(
                    "MAE's secure connection returned an invalid response. " +
                    "Please try the question again."
                );
            }

            if (!response.ok) {
                throw new Error(
                    payload.detail || "MAE could not complete the inquiry."
                );
            }
            mergeEntities(payload.entities);
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

        if (settings.speakResponse && voiceModeActive && answerToSpeak) {
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

    loadStatus();
    loadVoiceStatus();
})();
