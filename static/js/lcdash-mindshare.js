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
    let voiceChunks = [];
    let voiceFrame = null;
    let voiceCycleStarted = 0;
    let speechStarted = 0;
    let lastSpeechAt = 0;
    let speechDetected = false;
    let discardRecording = false;
    let activeAudioUrl = "";
    let activeSpeechController = null;
    let activeSpeechRequest = 0;

    if (!form || !questionInput || !messages) return;

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
            voiceReady = Boolean(
                response.ok &&
                status.connected &&
                status.tts &&
                status.tts.ready &&
                status.jack_tts &&
                status.jack_tts.ready &&
                status.stt &&
                status.stt.ready
            );
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

    async function speakAnswer(text) {
        const spokenText = String(text || "").trim().slice(0, 2500);
        if (!spokenText) return;

        activeSpeechRequest += 1;
        const requestId = activeSpeechRequest;
        if (activeSpeechController) activeSpeechController.abort();
        activeSpeechController = new AbortController();
        if (voicePlayer) {
            voicePlayer.pause();
            voicePlayer.removeAttribute("src");
            voicePlayer.load();
        }

        if (voiceModeActive) {
            setMicrophoneEnabled(false);
            setVoiceState(
                "speaking",
                "JACK is speaking",
                "The microphone is paused to prevent an echo."
            );
        }

        const response = await fetch("/api/voice/speech", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            cache: "no-store",
            signal: activeSpeechController.signal,
            body: JSON.stringify({
                text: spokenText,
                voice: "jack-synthetic-southern-male",
                speed: 0.92,
                response_format: "mp3"
            })
        });
        if (requestId !== activeSpeechRequest) return;
        if (!response.ok) {
            const payload = await response.json().catch(function () {
                return {};
            });
            throw new Error(
                payload.detail || "JACK could not generate speech."
            );
        }

        const audioBlob = await response.blob();
        if (activeAudioUrl) URL.revokeObjectURL(activeAudioUrl);
        activeAudioUrl = URL.createObjectURL(audioBlob);
        voicePlayer.src = activeAudioUrl;

        await new Promise(function (resolve, reject) {
            voicePlayer.onended = resolve;
            voicePlayer.onerror = function () {
                reject(new Error("The browser could not play JACK's voice."));
            };
            const playPromise = voicePlayer.play();
            if (playPromise) playPromise.catch(reject);
        });
        if (requestId === activeSpeechRequest) activeSpeechController = null;
    }

    async function transcribeRecording(blob) {
        const formData = new FormData();
        formData.append("file", blob, "jack-question.webm");
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

        const options = {};
        if (MediaRecorder.isTypeSupported("audio/webm;codecs=opus")) {
            options.mimeType = "audio/webm;codecs=opus";
        }
        mediaRecorder = new MediaRecorder(microphoneStream, options);
        mediaRecorder.addEventListener("dataavailable", function (event) {
            if (event.data.size) voiceChunks.push(event.data);
        });
        mediaRecorder.addEventListener("stop", async function () {
            const shouldSubmit =
                voiceModeActive && speechDetected && !discardRecording;
            const mimeType = mediaRecorder.mimeType || "audio/webm";
            const recording = new Blob(voiceChunks, {type: mimeType});
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
            audioContext
                .createMediaStreamSource(microphoneStream)
                .connect(analyser);
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
        activeSpeechRequest += 1;
        if (activeSpeechController) activeSpeechController.abort();
        activeSpeechController = null;
        if (voicePlayer) {
            voicePlayer.pause();
            voicePlayer.removeAttribute("src");
            voicePlayer.load();
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
            const heading = document.createElement("div");
            heading.className = "mae-evidence-heading";
            heading.textContent =
                item.title || item.file_name || "Mindshare document";
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

        if (role === "assistant") {
            const feedback = buildFeedback(payload && payload.interaction_id);
            if (feedback) bubble.appendChild(feedback);

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
                    await speakAnswer(content);
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
            const response = await fetch("/api/mindshare/chat", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                cache: "no-store",
                signal: controller.signal,
                body: JSON.stringify({
                    question: question,
                    history: requestHistory
                })
            });
            const responseText = await response.text();
            let payload = {};
            try {
                payload = responseText ? JSON.parse(responseText) : {};
            } catch (parseError) {
                throw new Error(
                    "JACK's secure connection returned an invalid response."
                );
            }
            if (!response.ok) {
                throw new Error(
                    payload.detail || "The inquiry could not be completed."
                );
            }
            addMessage("assistant", payload.answer, payload);
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

        if (settings.speakResponse && voiceModeActive && answerToSpeak) {
            try {
                await speakAnswer(answerToSpeak);
            } catch (error) {
                setVoiceState(
                    "error",
                    "I could not play the answer",
                    error.message ||
                    "The written answer is still available above."
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
