(function () {
    "use strict";

    const POLL_SECONDS = 5;
    const STORAGE_STATIONS = "lcdash.cloudStationAlerts.stations";
    const initialDataElement = document.getElementById("station-alert-data");
    const stationOptions = Array.from(document.querySelectorAll(".station-selector-option"));
    const selectorSummary = document.getElementById("station-selector-summary");
    const selectAllButton = document.getElementById("station-select-all");
    const clearAllButton = document.getElementById("station-clear-all");
    const overlay = document.getElementById("cloud-station-alert-overlay");
    const armButton = document.getElementById("arm-station-alerts");
    const testButton = document.getElementById("test-station-alert");
    const armStatus = document.getElementById("station-alert-arm-status");
    const message = document.getElementById("station-alert-message");
    const closeButton = document.getElementById("close-station-alert");

    let selectedStations = [];
    let firstSnapshot = true;
    let seenEventIds = new Set();
    let pendingAlerts = [];

    let soundArmed = false;
    let dispatchAudio = null;
    let confirmationAudio = null;
    let announcementAudio = null;
    let dispatchAudioUrl = "";
    let confirmationAudioUrl = "";
    let announcementAudioUrl = "";
    let announcementRequest = null;
    let announcementCycle = 0;
    let pendingAnnouncementText = "";
    let announcementReleasePending = false;
    let announcementError = "";

    function parseInitialData() {
        try {
            return JSON.parse(initialDataElement ? initialDataElement.textContent || "{}" : "{}");
        } catch (_error) {
            return {};
        }
    }

    function normalizeStations(values) {
        const result = [];
        const seen = new Set();
        (Array.isArray(values) ? values : [values]).forEach(function (value) {
            const station = String(value || "").trim();
            const key = station.toLowerCase();
            if (station && !seen.has(key)) {
                seen.add(key);
                result.push(station);
            }
        });
        return result;
    }

    function setText(id, value, fallback) {
        const element = document.getElementById(id);
        if (element) element.textContent = String(value || fallback || "—");
    }

    function stationLabel(stations) {
        if (!stations.length) return "None selected";
        if (stations.length <= 3) return stations.join(", ");
        return stations.slice(0, 2).join(", ") + " +" + (stations.length - 2) + " more";
    }

    function formatTime(value) {
        if (window.LCDashTime && typeof window.LCDashTime.formatCadDisplayTime === "function") {
            return window.LCDashTime.formatCadDisplayTime(value);
        }
        const date = new Date(value);
        return Number.isNaN(date.getTime()) ? String(value || "—") : date.toLocaleString("en-US", {
            timeZone: "America/New_York",
            timeZoneName: "short"
        });
    }

    function emptyState(messageText) {
        const element = document.createElement("div");
        element.className = "command-unavailable-state p-3 mt-3";
        element.textContent = messageText;
        return element;
    }

    function renderRows(containerId, rows, renderer, emptyMessage) {
        const container = document.getElementById(containerId);
        if (!container) return;
        container.replaceChildren();
        if (!rows.length) {
            container.appendChild(emptyState(emptyMessage));
            return;
        }
        rows.forEach(function (row) { container.appendChild(renderer(row)); });
    }

    function renderUnit(unit) {
        const row = document.createElement("div");
        row.className = "cloud-station-row";
        const identity = document.createElement("div");
        const number = document.createElement("strong");
        number.textContent = unit.unit_number || "Unit unavailable";
        const detail = document.createElement("div");
        detail.className = "text-secondary small";
        detail.textContent = [unit.station, unit.agency, unit.unit_type].filter(Boolean).join(" · ");
        identity.append(number, detail);
        const status = document.createElement("span");
        status.className = "badge text-bg-secondary align-self-start";
        status.textContent = unit.status || "Unknown";
        row.append(identity, status);
        return row;
    }

    function renderAssignment(alert) {
        const row = document.createElement("div");
        row.className = "cloud-station-row";
        const identity = document.createElement("div");
        const title = document.createElement("strong");
        title.textContent = alert.incident_description || "Assignment event";
        const detail = document.createElement("div");
        detail.className = "text-secondary small";
        detail.textContent = [alert.cfs_number, alert.location, (alert.unit_numbers || []).join(", ")].filter(Boolean).join(" · ");
        identity.append(title, detail);
        const time = document.createElement("span");
        time.className = "text-secondary small";
        time.textContent = formatTime(alert.dispatch_datetime);
        row.append(identity, time);
        return row;
    }

    function renderSnapshot(data) {
        const connected = data.connected === true;
        const connection = document.getElementById("station-connection-status");
        connection.textContent = connected ? "AVAILABLE" : "UNAVAILABLE";
        connection.className = "fw-bold " + (connected ? "text-success" : "text-warning");
        setText("station-last-updated", formatTime(data.generated_at));
        setText("station-name", stationLabel(data.selected_stations || selectedStations));
        setText("station-unit-count", String((data.station_units || []).length), "0");
        setText("station-active-count", String((data.alerts || []).length), "0");
        renderRows("station-units-list", data.station_units || [], renderUnit, "No units are available for the selected stations.");
        renderRows("station-current-alerts", data.alerts || [], renderAssignment, "No current assignment events are visible for the selected stations.");
    }

    // --- Fake alert tone: synthesized entirely in the browser, no audio
    // files and no dependency on real dispatch hardware or CAD. Ported from
    // the on-prem station-alerts tone generator.
    function writeWaveString(view, offset, value) {
        for (let index = 0; index < value.length; index += 1) {
            view.setUint8(offset + index, value.charCodeAt(index));
        }
    }

    function createToneWave(segments) {
        const sampleRate = 44100;
        const sampleCount = segments.reduce(function (total, segment) {
            return total + Math.round(segment.duration * sampleRate);
        }, 0);
        const buffer = new ArrayBuffer(44 + sampleCount * 2);
        const view = new DataView(buffer);

        writeWaveString(view, 0, "RIFF");
        view.setUint32(4, 36 + sampleCount * 2, true);
        writeWaveString(view, 8, "WAVE");
        writeWaveString(view, 12, "fmt ");
        view.setUint32(16, 16, true);
        view.setUint16(20, 1, true);
        view.setUint16(22, 1, true);
        view.setUint32(24, sampleRate, true);
        view.setUint32(28, sampleRate * 2, true);
        view.setUint16(32, 2, true);
        view.setUint16(34, 16, true);
        writeWaveString(view, 36, "data");
        view.setUint32(40, sampleCount * 2, true);

        let outputSample = 0;
        let phase = 0;
        segments.forEach(function (segment) {
            const segmentSamples = Math.round(segment.duration * sampleRate);
            const fadeSamples = Math.min(Math.round(0.012 * sampleRate), Math.floor(segmentSamples / 4));
            const startFrequency = Number(segment.frequency || 0);
            const endFrequency = Number(
                segment.endFrequency === undefined ? startFrequency : segment.endFrequency
            );

            for (let localSample = 0; localSample < segmentSamples; localSample += 1) {
                let sampleValue = 0;
                if (startFrequency > 0) {
                    const fadeIn = fadeSamples ? Math.min(1, localSample / fadeSamples) : 1;
                    const fadeOut = fadeSamples
                        ? Math.min(1, (segmentSamples - localSample - 1) / fadeSamples)
                        : 1;
                    const envelope = Math.max(0, Math.min(fadeIn, fadeOut));
                    const progress = segmentSamples > 1 ? localSample / (segmentSamples - 1) : 0;
                    const frequency = startFrequency + (endFrequency - startFrequency) * progress;
                    phase += 2 * Math.PI * frequency / sampleRate;
                    sampleValue = Math.sin(phase) * (segment.amplitude || 0.75) * envelope;
                } else {
                    phase = 0;
                }
                view.setInt16(44 + outputSample * 2, sampleValue * 32767, true);
                outputSample += 1;
            }
        });

        return new Blob([buffer], { type: "audio/wav" });
    }

    function ensureAudioPlayers() {
        if (!dispatchAudio) {
            dispatchAudioUrl = URL.createObjectURL(createToneWave([
                { frequency: 600, duration: 1.0, amplitude: 0.82 },
                { frequency: 0, duration: 0.12, amplitude: 0 },
                { frequency: 900, duration: 3.0, amplitude: 0.82 },
                { frequency: 0, duration: 0.16, amplitude: 0 },
                { frequency: 1450, duration: 0.2, amplitude: 0.9 },
                { frequency: 0, duration: 0.12, amplitude: 0 },
                { frequency: 1450, duration: 0.2, amplitude: 0.9 },
                { frequency: 0, duration: 0.12, amplitude: 0 },
                { frequency: 1450, duration: 0.2, amplitude: 0.9 },
                { frequency: 0, duration: 0.15, amplitude: 0 },
                { frequency: 650, endFrequency: 1350, duration: 0.55, amplitude: 0.86 },
                { frequency: 1350, endFrequency: 650, duration: 0.55, amplitude: 0.86 },
                { frequency: 650, endFrequency: 1350, duration: 0.55, amplitude: 0.86 },
                { frequency: 1350, endFrequency: 650, duration: 0.55, amplitude: 0.86 },
                { frequency: 650, endFrequency: 1350, duration: 0.55, amplitude: 0.86 },
                { frequency: 1350, endFrequency: 650, duration: 0.55, amplitude: 0.86 }
            ]));
            dispatchAudio = new Audio(dispatchAudioUrl);
            dispatchAudio.setAttribute("aria-hidden", "true");
            dispatchAudio.preload = "auto";
            dispatchAudio.volume = 1;
            dispatchAudio.addEventListener("play", function () {
                setArmStatus("playing", "PAGING AUDIO PLAYING");
            });
            dispatchAudio.addEventListener("ended", function () {
                const announcement = pendingAnnouncementText;
                pendingAnnouncementText = "";
                if (announcement) {
                    releasePreparedAnnouncement();
                } else {
                    updateArmedDisplay();
                }
            });
        }
        if (!confirmationAudio) {
            confirmationAudioUrl = URL.createObjectURL(createToneWave([
                { frequency: 880, duration: 0.16, amplitude: 0.48 },
                { frequency: 0, duration: 0.06, amplitude: 0 },
                { frequency: 1175, duration: 0.18, amplitude: 0.48 }
            ]));
            confirmationAudio = new Audio(confirmationAudioUrl);
            confirmationAudio.preload = "auto";
            confirmationAudio.volume = 1;
        }
    }

    function setArmStatus(state, label) {
        if (!armStatus) return;
        armStatus.dataset.audioState = state;
        armStatus.innerHTML = '<span class="status-dot"></span> ' + label;
    }

    function updateArmedDisplay() {
        if (armStatus) {
            armStatus.classList.toggle("armed", soundArmed);
            setArmStatus(soundArmed ? "armed" : "disarmed", soundArmed ? "SOUND ARMED" : "SOUND DISARMED");
        }
        if (armButton) {
            armButton.classList.toggle("btn-outline-info", !soundArmed);
            armButton.classList.toggle("btn-success", soundArmed);
            armButton.innerHTML = soundArmed
                ? '<i class="bi bi-volume-up-fill"></i> Loud Alerts Enabled'
                : '<i class="bi bi-volume-up-fill"></i> Enable Loud Alerts';
        }
    }

    function playAudio(audio, failureMessage) {
        audio.pause();
        audio.currentTime = 0;
        const playback = audio.play();
        if (playback && typeof playback.catch === "function") {
            playback.catch(function (error) {
                soundArmed = false;
                updateArmedDisplay();
                if (message) message.textContent = failureMessage + " Browser message: " + error.message;
            });
        }
        return playback;
    }

    function armSound(options) {
        const settings = options || {};
        ensureAudioPlayers();
        soundArmed = true;
        updateArmedDisplay();
        if (message) {
            message.textContent = "Paging tone and spoken announcement are enabled for this browser. " +
                "This is a supplementary alert display, not a substitute for your station's primary dispatch notification.";
        }
        if (soundArmed && settings.confirm !== false) {
            playAudio(confirmationAudio, "The browser blocked the confirmation sound.");
        }
        return soundArmed;
    }

    function stopAnnouncement() {
        announcementCycle += 1;
        pendingAnnouncementText = "";
        announcementReleasePending = false;
        announcementError = "";
        if (announcementRequest) {
            announcementRequest.abort();
            announcementRequest = null;
        }
        if (announcementAudio) {
            announcementAudio.pause();
            announcementAudio.currentTime = 0;
            announcementAudio = null;
        }
        if (announcementAudioUrl) {
            URL.revokeObjectURL(announcementAudioUrl);
            announcementAudioUrl = "";
        }
    }

    function stopTone() {
        [dispatchAudio, confirmationAudio].forEach(function (audio) {
            if (!audio) return;
            audio.pause();
            audio.currentTime = 0;
        });
        stopAnnouncement();
    }

    function releasePreparedAnnouncement() {
        announcementReleasePending = true;
        if (!soundArmed || !overlay.classList.contains("visible")) {
            updateArmedDisplay();
            return;
        }
        if (!announcementAudio) {
            if (announcementError) {
                if (message) message.textContent = "Paging tone completed, but the announcement could not be spoken: " + announcementError;
                updateArmedDisplay();
            } else {
                setArmStatus("finalizing", "FINALIZING ANNOUNCEMENT");
            }
            return;
        }
        announcementReleasePending = false;
        const playback = announcementAudio.play();
        if (playback && typeof playback.catch === "function") {
            playback.catch(function (error) {
                if (soundArmed && message) {
                    message.textContent = "Paging tone completed, but the announcement could not be spoken: " + error.message;
                }
                stopAnnouncement();
                updateArmedDisplay();
            });
        }
    }

    // Cloud voice uses the same advisory speech endpoint MAE's Listen button
    // uses (Amazon Polly), not on-prem's local Speaches service.
    async function prepareAnnouncement(announcement) {
        const spokenText = String(announcement || "").trim();
        if (!spokenText || !soundArmed || !overlay.classList.contains("visible")) {
            updateArmedDisplay();
            return;
        }

        const cycle = announcementCycle;
        announcementRequest = new AbortController();
        setArmStatus("generating", "GENERATING ANNOUNCEMENT");

        try {
            const response = await fetch("/api/cloud-ai/speech/sentence", {
                method: "POST",
                cache: "no-store",
                headers: {
                    "Accept": "audio/mpeg",
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({ text: spokenText, persona: "mae", voice: "" }),
                signal: announcementRequest.signal
            });
            if (!response.ok) throw new Error("cloud speech returned " + response.status);

            const audioBlob = await response.blob();
            if (cycle !== announcementCycle || !soundArmed || !overlay.classList.contains("visible")) {
                return;
            }

            announcementRequest = null;
            announcementAudioUrl = URL.createObjectURL(audioBlob);
            announcementAudio = new Audio(announcementAudioUrl);
            announcementAudio.preload = "auto";
            announcementAudio.volume = 1;
            announcementAudio.load();
            announcementAudio.addEventListener("play", function () {
                setArmStatus("speaking", "ANNOUNCEMENT PLAYING");
            });
            announcementAudio.addEventListener("ended", function () {
                stopAnnouncement();
                updateArmedDisplay();
            });

            if (announcementReleasePending) {
                releasePreparedAnnouncement();
            } else {
                setArmStatus("ready", "ANNOUNCEMENT READY");
            }
        } catch (error) {
            if (error.name !== "AbortError" && cycle === announcementCycle) {
                announcementRequest = null;
                announcementError = error.message;
                if (announcementReleasePending) {
                    releasePreparedAnnouncement();
                } else {
                    setArmStatus("unavailable", "ANNOUNCEMENT UNAVAILABLE");
                }
            }
        }
    }

    function playDispatchTone(announcement) {
        if (!soundArmed) return;
        ensureAudioPlayers();
        stopTone();
        pendingAnnouncementText = String(announcement || "").trim();
        if (pendingAnnouncementText) prepareAnnouncement(pendingAnnouncementText);
        playAudio(dispatchAudio, "The browser blocked the station alert tone. Check the tab sound permission.");
    }
    // --- end fake alert tone

    function showVisualAlert(alert) {
        setText("alert-station-name", stationLabel(alert.station_names || selectedStations), "Selected stations");
        setText("alert-incident-code", alert.incident_code, "ASSIGNMENT EVENT");
        setText("alert-incident-title", alert.incident_description, "Assignment event");
        setText("alert-location", alert.location, "Location unavailable");
        setText("alert-units", (alert.unit_numbers || []).join(", "), "Unit unavailable");
        setText("alert-cfs-number", alert.cfs_number, "Unavailable");
        setText("alert-dispatch-time", formatTime(alert.dispatch_datetime));

        const soundNotice = document.getElementById("alert-sound-notice");
        if (soundNotice) {
            soundNotice.textContent = soundArmed
                ? ""
                : "VISUAL ALERT ONLY — click Enable Loud Alerts for the tone and spoken announcement.";
        }

        overlay.classList.add("visible");
        if (soundArmed) playDispatchTone(alert.announcement);
    }

    function hideAlert() {
        overlay.classList.remove("visible");
        stopTone();
        if (pendingAlerts.length) {
            const nextAlert = pendingAlerts.shift();
            window.setTimeout(function () { showVisualAlert(nextAlert); }, 250);
        }
    }

    function testAlert() {
        if (testButton) testButton.disabled = true;
        const armed = armSound({ confirm: false });
        if (!armed) {
            if (message) message.textContent = "The browser could not start audio. Check the browser sound permission and computer volume.";
            if (testButton) testButton.disabled = false;
            return;
        }

        const now = new Date();
        const stationNumber = String(selectedStations[0] || "100")
            .replace(/^(station|sta)\s*[-:#]?\s*/i, "")
            .trim();
        const testTime = String(now.getHours()).padStart(2, "0") + String(now.getMinutes()).padStart(2, "0");
        const demo = {
            incident_code: "TEST",
            incident_description: "TEST — Commercial Structure Fire",
            location: "911 Mark Spurlock Drive, Logan, WV 25601",
            unit_numbers: ["TEST ENG 1", "TEST LAD 1", "TEST MEDIC 1"],
            station_names: selectedStations.length ? selectedStations : ["TEST STATION"],
            cfs_number: "TEST-CFS-STRUCTURE-FIRE",
            dispatch_datetime: now.toISOString(),
            announcement: "Station " + stationNumber +
                ", respond to 911 Mark Spurlock Drive for a test commercial structure fire. Time is " +
                testTime + "."
        };
        showVisualAlert(demo);
        window.setTimeout(function () { if (testButton) testButton.disabled = false; }, 500);
    }

    function detectNewAssignments(alerts) {
        const incoming = Array.isArray(alerts) ? alerts : [];
        if (firstSnapshot) {
            incoming.forEach(function (alert) { if (alert.event_id) seenEventIds.add(alert.event_id); });
            firstSnapshot = false;
            return;
        }
        const newAlerts = incoming.filter(function (alert) {
            return alert.event_id && !seenEventIds.has(alert.event_id);
        });
        incoming.forEach(function (alert) { if (alert.event_id) seenEventIds.add(alert.event_id); });
        if (!newAlerts.length) return;
        if (overlay.classList.contains("visible")) {
            pendingAlerts.push.apply(pendingAlerts, newAlerts);
        } else {
            showVisualAlert(newAlerts[0]);
            pendingAlerts.push.apply(pendingAlerts, newAlerts.slice(1));
        }
    }

    async function loadSnapshot() {
        if (!selectedStations.length) {
            renderSnapshot({connected: true, selected_stations: [], generated_at: new Date().toISOString(), station_units: [], alerts: []});
            return;
        }
        const query = new URLSearchParams();
        selectedStations.forEach(function (station) { query.append("station", station); });
        try {
            const response = await fetch("/api/operations/station-alerts?" + query.toString(), {
                cache: "no-store",
                headers: {"Accept": "application/json"}
            });
            if (!response.ok) throw new Error("Read-only assignment source unavailable");
            const data = await response.json();
            renderSnapshot(data);
            detectNewAssignments(data.alerts);
        } catch (_error) {
            renderSnapshot({connected: false, selected_stations: selectedStations, generated_at: new Date().toISOString(), station_units: [], alerts: []});
        }
    }

    function updateSelectionUi() {
        const selectedKeys = new Set(selectedStations.map(function (station) { return station.toLowerCase(); }));
        stationOptions.forEach(function (option) { option.checked = selectedKeys.has(option.value.toLowerCase()); });
        selectorSummary.textContent = selectedStations.length ? stationLabel(selectedStations) : "Choose one or more stations...";
    }

    function chooseStations(stations) {
        selectedStations = normalizeStations(stations);
        localStorage.setItem(STORAGE_STATIONS, JSON.stringify(selectedStations));
        updateSelectionUi();
        firstSnapshot = true;
        seenEventIds = new Set();
        pendingAlerts = [];
        hideAlert();
        const url = new URL(window.location.href);
        url.searchParams.delete("station");
        selectedStations.forEach(function (station) { url.searchParams.append("station", station); });
        window.history.replaceState({}, "", url);
        loadSnapshot();
    }

    const initialData = parseInitialData();
    let stored = [];
    try { stored = JSON.parse(localStorage.getItem(STORAGE_STATIONS) || "[]"); } catch (_error) { stored = []; }
    const requested = normalizeStations((initialData.selected_stations || []).length ? initialData.selected_stations : stored);
    const choices = new Map(stationOptions.map(function (option) { return [option.value.toLowerCase(), option.value]; }));
    selectedStations = requested.map(function (station) { return choices.get(station.toLowerCase()); }).filter(Boolean);
    updateSelectionUi();

    stationOptions.forEach(function (option) {
        option.addEventListener("change", function () {
            chooseStations(stationOptions.filter(function (candidate) { return candidate.checked; }).map(function (candidate) { return candidate.value; }));
        });
    });
    if (selectAllButton) selectAllButton.addEventListener("click", function () { chooseStations(stationOptions.map(function (option) { return option.value; })); });
    if (clearAllButton) clearAllButton.addEventListener("click", function () { chooseStations([]); });
    if (armButton) armButton.addEventListener("click", function () { armSound(); });
    if (testButton) testButton.addEventListener("click", testAlert);
    if (closeButton) closeButton.addEventListener("click", hideAlert);
    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && overlay.classList.contains("visible")) hideAlert();
    });

    renderSnapshot(initialData);
    const snapshotStations = normalizeStations(initialData.selected_stations || []);
    const snapshotMatches = selectedStations.length === snapshotStations.length &&
        selectedStations.every(function (station, index) {
            return station.toLowerCase() === String(snapshotStations[index] || "").toLowerCase();
        });
    if (snapshotMatches) {
        detectNewAssignments(initialData.alerts);
    } else if (selectedStations.length) {
        loadSnapshot();
    } else {
        firstSnapshot = false;
    }
    window.setInterval(loadSnapshot, POLL_SECONDS * 1000);
}());
