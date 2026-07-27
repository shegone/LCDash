(function () {
    const statusPanel = document.getElementById("voice-status");
    const speakButton = document.getElementById("voice-speak");
    const textInput = document.getElementById("voice-text");
    const voiceChoice = document.getElementById("voice-choice");
    const voiceSpeed = document.getElementById("voice-speed");
    const player = document.getElementById("voice-player");
    const ttsMessage = document.getElementById("voice-tts-message");
    const recordButton = document.getElementById("voice-record");
    const recordTime = document.getElementById("voice-record-time");
    const transcript = document.getElementById("voice-transcript");
    const sttMessage = document.getElementById("voice-stt-message");

    let mediaRecorder = null;
    let chunks = [];
    let timerId = null;
    let recordingStarted = 0;

    function setMessage(element, message, isError) {
        element.textContent = message || "";
        element.classList.toggle("is-error", Boolean(isError));
    }

    async function loadStatus() {
        try {
            const response = await fetch("/api/voice/status", {cache: "no-store"});
            const payload = await response.json();
            const ready = payload.connected && payload.tts.ready && payload.stt.ready;
            statusPanel.classList.toggle("is-online", ready);
            statusPanel.querySelector("strong").textContent = ready
                ? "Ready"
                : payload.connected
                    ? "Models loading"
                    : "Offline";
        } catch (error) {
            statusPanel.querySelector("strong").textContent = "Unavailable";
        }
    }

    speakButton.addEventListener("click", async function () {
        const text = textInput.value.trim();
        if (!text) {
            setMessage(ttsMessage, "Enter something for MAE to say.", true);
            return;
        }

        speakButton.disabled = true;
        setMessage(ttsMessage, "Generating speech locally…", false);

        try {
            const response = await fetch("/api/voice/speech", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({
                    text: text,
                    voice: voiceChoice.value,
                    speed: Number(voiceSpeed.value),
                    response_format: "mp3"
                })
            });
            if (!response.ok) {
                const payload = await response.json();
                throw new Error(payload.detail || "Speech generation failed.");
            }

            const audioBlob = await response.blob();
            if (player.src) {
                URL.revokeObjectURL(player.src);
            }
            player.src = URL.createObjectURL(audioBlob);
            player.hidden = false;
            await player.play();
            setMessage(ttsMessage, "Generated entirely on the local voice stack.", false);
        } catch (error) {
            setMessage(ttsMessage, error.message, true);
        } finally {
            speakButton.disabled = false;
        }
    });

    function updateRecordTimer() {
        const elapsed = Math.floor((Date.now() - recordingStarted) / 1000);
        const minutes = String(Math.floor(elapsed / 60)).padStart(2, "0");
        const seconds = String(elapsed % 60).padStart(2, "0");
        recordTime.textContent = `${minutes}:${seconds}`;
    }

    async function submitRecording(blob) {
        setMessage(sttMessage, "Transcribing locally…", false);
        const formData = new FormData();
        formData.append("file", blob, "voice-test.webm");

        try {
            const response = await fetch("/api/voice/transcribe", {
                method: "POST",
                body: formData
            });
            const payload = await response.json();
            if (!response.ok) {
                throw new Error(payload.detail || "Transcription failed.");
            }
            transcript.textContent = payload.text || "No speech was detected.";
            setMessage(sttMessage, "Recording processed and discarded.", false);
        } catch (error) {
            setMessage(sttMessage, error.message, true);
        }
    }

    recordButton.addEventListener("click", async function () {
        if (mediaRecorder && mediaRecorder.state === "recording") {
            mediaRecorder.stop();
            recordButton.classList.remove("is-recording");
            recordButton.innerHTML = '<span class="voice-record-dot"></span> Start recording';
            clearInterval(timerId);
            return;
        }

        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            setMessage(sttMessage, "This browser does not provide microphone access.", true);
            return;
        }

        try {
            const stream = await navigator.mediaDevices.getUserMedia({audio: true});
            chunks = [];
            mediaRecorder = new MediaRecorder(stream);
            mediaRecorder.addEventListener("dataavailable", function (event) {
                if (event.data.size > 0) {
                    chunks.push(event.data);
                }
            });
            mediaRecorder.addEventListener("stop", function () {
                stream.getTracks().forEach(function (track) { track.stop(); });
                const blob = new Blob(chunks, {type: mediaRecorder.mimeType || "audio/webm"});
                submitRecording(blob);
            });
            mediaRecorder.start();
            recordingStarted = Date.now();
            updateRecordTimer();
            timerId = setInterval(updateRecordTimer, 1000);
            recordButton.classList.add("is-recording");
            recordButton.innerHTML = '<span class="voice-record-dot"></span> Stop and transcribe';
            setMessage(sttMessage, "Listening…", false);
        } catch (error) {
            setMessage(sttMessage, "Microphone permission was not granted.", true);
        }
    });

    loadStatus();
})();
