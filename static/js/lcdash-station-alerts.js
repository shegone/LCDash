(function () {
    "use strict";

    const POLL_SECONDS = 5;
    const STORAGE_STATION = "lcdash.stationAlerts.station";

    const selector = document.getElementById("station-selector");
    const armButton = document.getElementById("arm-station-alerts");
    const testButton = document.getElementById("test-station-alert");
    const armStatus = document.getElementById("station-alert-arm-status");
    const message = document.getElementById("station-alert-message");
    const overlay = document.getElementById("dispatch-alert-overlay");
    const acknowledgeButton = document.getElementById("acknowledge-station-alert");
    const initialDataElement = document.getElementById("station-alert-data");

    let selectedStation = "";
    let soundArmed = false;
    let dispatchAudio = null;
    let confirmationAudio = null;
    let dispatchAudioUrl = "";
    let confirmationAudioUrl = "";
    let alertMap = null;
    let pollTimer = null;
    let firstSnapshotForStation = true;
    let seenEventIds = new Set();

    function text(id, value) {
        const element = document.getElementById(id);
        if (element) {
            element.textContent = value || "—";
        }
    }

    function parseInitialData() {
        if (!initialDataElement) {
            return {};
        }

        try {
            return JSON.parse(initialDataElement.textContent || "{}");
        } catch (_error) {
            return {};
        }
    }

    function formatCadTime(value) {
        if (window.LCDashTime && typeof window.LCDashTime.formatCadDisplayTime === "function") {
            return window.LCDashTime.formatCadDisplayTime(value);
        }

        const date = new Date(value);
        return Number.isNaN(date.getTime()) ? (value || "—") : date.toLocaleString();
    }

    function createEmpty(messageText) {
        const empty = document.createElement("div");
        empty.className = "empty-state";
        empty.textContent = messageText;
        return empty;
    }

    function renderUnits(units) {
        const container = document.getElementById("station-units-list");
        if (!container) {
            return;
        }

        container.replaceChildren();
        if (!units || !units.length) {
            container.appendChild(createEmpty(
                selectedStation ? "No units are assigned to this CAD station." : "Choose a station to view its units."
            ));
            return;
        }

        units.forEach(function (unit) {
            const row = document.createElement("div");
            row.className = "station-unit-row";

            const number = document.createElement("div");
            number.className = "unit-number-strong";
            number.textContent = unit.unit_number || "Unknown unit";

            const statusWrap = document.createElement("div");
            const status = document.createElement("span");
            status.className = "unit-status-chip";
            status.textContent = unit.status || "Unknown";
            statusWrap.appendChild(status);

            const assignment = document.createElement("div");
            assignment.className = unit.cfs_number ? "text-warning fw-bold" : "text-secondary";
            assignment.textContent = unit.cfs_number ? ("Assigned: " + unit.cfs_number) : "Not assigned";

            row.append(number, statusWrap, assignment);
            container.appendChild(row);
        });
    }

    function renderAssignments(alerts) {
        const container = document.getElementById("station-current-alerts");
        if (!container) {
            return;
        }

        container.replaceChildren();
        if (!alerts || !alerts.length) {
            container.appendChild(createEmpty(
                selectedStation ? "No active CAD assignments for this station." : "Choose a station to begin monitoring."
            ));
            return;
        }

        alerts.forEach(function (alert) {
            const row = document.createElement("div");
            row.className = "station-call-row";

            const incident = document.createElement("div");
            const title = document.createElement("div");
            title.className = "unit-number-strong";
            title.textContent = alert.incident_description || "CAD Dispatch";
            const code = document.createElement("div");
            code.className = "text-info small";
            code.textContent = [alert.incident_code, alert.cfs_number].filter(Boolean).join(" • ");
            incident.append(title, code);

            const units = document.createElement("div");
            units.className = "fw-bold text-warning";
            units.textContent = (alert.unit_numbers || []).join(", ") || "Unit unavailable";

            const location = document.createElement("div");
            location.className = "text-light";
            location.textContent = alert.location || "Location unavailable";

            row.append(incident, units, location);
            container.appendChild(row);
        });
    }

    function renderSnapshot(data) {
        const connected = Boolean(data.connected);
        const connection = document.getElementById("station-connection-status");
        if (connection) {
            connection.textContent = connected ? "CONNECTED" : "DISCONNECTED";
            connection.className = "fw-bold " + (connected ? "text-success" : "text-danger");
        }

        text("station-name", data.selected_station || "None selected");
        text("station-unit-count", String((data.station_units || []).length));
        text("station-active-count", String((data.alerts || []).length));

        const updated = document.getElementById("station-last-updated");
        if (updated) {
            updated.textContent = formatCadTime(data.generated_at);
        }

        renderUnits(data.station_units || []);
        renderAssignments(data.alerts || []);

        if (data.roster_warning) {
            message.textContent = data.roster_warning;
        } else {
            message.textContent = soundArmed
                ? "Traditional two-tone paging audio is enabled. Keep this page open and the computer volume turned up."
                : "“Test Two-Tone Alert” will enable browser audio and play the complete paging sequence.";
        }
    }

    function stopTone() {
        [dispatchAudio, confirmationAudio].forEach(function (audio) {
            if (!audio) {
                return;
            }
            audio.pause();
            audio.currentTime = 0;
        });
    }

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
        segments.forEach(function (segment) {
            const segmentSamples = Math.round(segment.duration * sampleRate);
            const fadeSamples = Math.min(Math.round(0.012 * sampleRate), Math.floor(segmentSamples / 4));

            for (let localSample = 0; localSample < segmentSamples; localSample += 1) {
                let sampleValue = 0;
                if (segment.frequency > 0) {
                    const fadeIn = fadeSamples ? Math.min(1, localSample / fadeSamples) : 1;
                    const fadeOut = fadeSamples
                        ? Math.min(1, (segmentSamples - localSample - 1) / fadeSamples)
                        : 1;
                    const envelope = Math.max(0, Math.min(fadeIn, fadeOut));
                    sampleValue = Math.sin(
                        2 * Math.PI * segment.frequency * localSample / sampleRate
                    ) * (segment.amplitude || 0.75) * envelope;
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
                { frequency: 900, duration: 3.0, amplitude: 0.82 }
            ]));
            dispatchAudio = new Audio(dispatchAudioUrl);
            dispatchAudio.id = "station-alert-audio";
            dispatchAudio.setAttribute("aria-hidden", "true");
            dispatchAudio.style.display = "none";
            dispatchAudio.preload = "auto";
            dispatchAudio.volume = 1;
            document.body.appendChild(dispatchAudio);
            dispatchAudio.addEventListener("play", function () {
                armStatus.dataset.audioState = "playing";
                armStatus.innerHTML = '<span class="status-dot"></span> PAGING AUDIO PLAYING';
            });
            dispatchAudio.addEventListener("ended", function () {
                armStatus.dataset.audioState = "ready";
                updateArmedDisplay();
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

    function updateArmedDisplay() {
        armStatus.classList.toggle("armed", soundArmed);
        armStatus.innerHTML = '<span class="status-dot"></span>' +
            (soundArmed ? " SOUND ARMED" : " SOUND DISARMED");
        armButton.classList.toggle("btn-danger", !soundArmed);
        armButton.classList.toggle("btn-success", soundArmed);
        armButton.innerHTML = soundArmed
            ? '<i class="bi bi-volume-up-fill"></i> Loud Alerts Enabled'
            : '<i class="bi bi-volume-up-fill"></i> Enable Loud Alerts';
    }

    function playAudio(audio, failureMessage) {
        audio.pause();
        audio.currentTime = 0;
        const playback = audio.play();

        if (playback && typeof playback.catch === "function") {
            playback.catch(function (error) {
                soundArmed = false;
                updateArmedDisplay();
                message.textContent = failureMessage + " Browser message: " + error.message;
            });
        }

        return playback;
    }

    function armSound(options) {
        const settings = options || {};
        ensureAudioPlayers();
        soundArmed = true;
        updateArmedDisplay();
        message.textContent = soundArmed
            ? "Traditional two-tone paging audio is enabled. Keep this page open and the computer volume turned up."
            : "Audio could not be enabled. Click the button again.";

        if (soundArmed && settings.confirm !== false) {
            playAudio(
                confirmationAudio,
                "The browser blocked the confirmation sound."
            );
        }
        return soundArmed;
    }

    function playDispatchTone() {
        if (!soundArmed) {
            return;
        }

        ensureAudioPlayers();
        stopTone();
        playAudio(
            dispatchAudio,
            "The browser blocked the two-tone page. Check the tab sound permission."
        );
    }

    function validCoordinate(value, minimum, maximum) {
        if (value === null || value === undefined || String(value).trim() === "") {
            return false;
        }
        const number = Number(value);
        return Number.isFinite(number) && number >= minimum && number <= maximum;
    }

    function renderAlertMap(alert) {
        const mapWrap = document.getElementById("alert-map-wrap");
        const unavailable = document.getElementById("alert-map-unavailable");
        const hasCoordinates = validCoordinate(alert.latitude, -90, 90) &&
            validCoordinate(alert.longitude, -180, 180) &&
            !(Number(alert.latitude) === 0 && Number(alert.longitude) === 0);

        if (alertMap) {
            alertMap.remove();
            alertMap = null;
        }

        mapWrap.classList.toggle("d-none", !hasCoordinates);
        unavailable.classList.toggle("d-none", hasCoordinates);
        if (!hasCoordinates || !window.L) {
            return;
        }

        alertMap = L.map("alert-map", {
            zoomControl: true,
            attributionControl: true
        }).setView([Number(alert.latitude), Number(alert.longitude)], 16);

        L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
            maxZoom: 19,
            attribution: "&copy; OpenStreetMap contributors"
        }).addTo(alertMap);

        L.marker([Number(alert.latitude), Number(alert.longitude)])
            .addTo(alertMap)
            .bindPopup(alert.location || "CAD incident")
            .openPopup();

        window.setTimeout(function () {
            alertMap.invalidateSize();
        }, 120);
    }

    function showAlert(alert, isTest) {
        text("alert-station-name", selectedStation || "Selected Station");
        text("alert-incident-code", alert.incident_code || "CAD DISPATCH");
        text("alert-incident-title", alert.incident_description || "CAD Dispatch");
        text("alert-priority", alert.priority ? ("PRI " + alert.priority) : "PRI —");
        text("alert-location", alert.location || "Location unavailable");
        text("alert-units", (alert.unit_numbers || []).join(", ") || "Unit unavailable");
        text("alert-cfs-number", alert.cfs_number || "TEST ALERT");
        text("alert-dispatch-time", formatCadTime(alert.dispatch_datetime || new Date().toISOString()));

        const soundNotice = document.getElementById("alert-sound-notice");
        soundNotice.textContent = soundArmed
            ? (isTest ? "TEST MODE — loud alert tone enabled." : "")
            : "VISUAL ALERT ONLY — click Enable Loud Alerts for audio.";

        overlay.classList.add("visible");
        renderAlertMap(alert);
        if (soundArmed) {
            playDispatchTone();
        }
    }

    function hideAlert() {
        overlay.classList.remove("visible");
        stopTone();
        if (alertMap) {
            alertMap.remove();
            alertMap = null;
        }
    }

    function testAlert() {
        testButton.disabled = true;
        const armed = armSound({ confirm: false });

        if (!armed) {
            message.textContent = "The browser could not start audio. Check the browser sound permission and computer volume.";
            testButton.disabled = false;
            return;
        }

        const demo = {
            incident_code: "TEST",
            incident_description: "Station Alert Test",
            priority: "10",
            location: "This is only a test of the LCDash station display.",
            unit_numbers: ["TEST UNIT"],
            cfs_number: "TEST-ALERT",
            dispatch_datetime: new Date().toISOString(),
            latitude: null,
            longitude: null
        };
        showAlert(demo, true);
        window.setTimeout(function () {
            testButton.disabled = false;
        }, 500);
    }

    function baselineOrAlert(alerts) {
        const incoming = alerts || [];
        if (firstSnapshotForStation) {
            incoming.forEach(function (alert) {
                if (alert.event_id) {
                    seenEventIds.add(alert.event_id);
                }
            });
            firstSnapshotForStation = false;
            return;
        }

        const newAlerts = incoming.filter(function (alert) {
            return alert.event_id && !seenEventIds.has(alert.event_id);
        });

        incoming.forEach(function (alert) {
            if (alert.event_id) {
                seenEventIds.add(alert.event_id);
            }
        });

        if (newAlerts.length) {
            showAlert(newAlerts[0], false);
        }
    }

    async function loadSnapshot() {
        if (!selectedStation) {
            renderSnapshot({
                connected: true,
                selected_station: "",
                generated_at: new Date().toISOString(),
                station_units: [],
                alerts: []
            });
            return;
        }

        try {
            const response = await fetch(
                "/api/operations/station-alerts?station=" + encodeURIComponent(selectedStation),
                { cache: "no-store", headers: { "Accept": "application/json" } }
            );
            if (!response.ok) {
                throw new Error("CAD request returned " + response.status);
            }

            const data = await response.json();
            renderSnapshot(data);
            baselineOrAlert(data.alerts);
        } catch (error) {
            const connection = document.getElementById("station-connection-status");
            connection.textContent = "DISCONNECTED";
            connection.className = "fw-bold text-danger";
            message.textContent = "Station alert check failed: " + error.message;
        }
    }

    function chooseStation(station) {
        selectedStation = station || "";
        localStorage.setItem(STORAGE_STATION, selectedStation);
        seenEventIds = new Set();
        firstSnapshotForStation = true;
        hideAlert();

        const url = new URL(window.location.href);
        if (selectedStation) {
            url.searchParams.set("station", selectedStation);
        } else {
            url.searchParams.delete("station");
        }
        window.history.replaceState({}, "", url);
        loadSnapshot();
    }

    function startPolling() {
        if (pollTimer) {
            window.clearInterval(pollTimer);
        }
        pollTimer = window.setInterval(loadSnapshot, POLL_SECONDS * 1000);
    }

    const initialData = parseInitialData();
    const storedStation = localStorage.getItem(STORAGE_STATION) || "";
    selectedStation = initialData.selected_station || storedStation;

    if (selectedStation && selector) {
        const matchingOption = Array.from(selector.options).find(function (option) {
            return option.value.toLowerCase() === selectedStation.toLowerCase();
        });
        if (matchingOption) {
            selector.value = matchingOption.value;
            selectedStation = matchingOption.value;
        } else {
            selectedStation = "";
        }
    }

    selector.addEventListener("change", function () {
        chooseStation(selector.value);
    });
    armButton.addEventListener("click", armSound);
    testButton.addEventListener("click", testAlert);
    acknowledgeButton.addEventListener("click", hideAlert);
    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && overlay.classList.contains("visible")) {
            hideAlert();
        }
    });

    renderSnapshot(initialData);
    if (
        selectedStation &&
        initialData.selected_station &&
        selectedStation.toLowerCase() === initialData.selected_station.toLowerCase()
    ) {
        baselineOrAlert(initialData.alerts || []);
    }
    startPolling();
})();
