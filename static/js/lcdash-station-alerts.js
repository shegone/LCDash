(function () {
    "use strict";

    const POLL_SECONDS = 5;
    const STORAGE_STATIONS = "lcdash.stationAlerts.stations";
    const LEGACY_STORAGE_STATION = "lcdash.stationAlerts.station";

    const selector = document.getElementById("station-selector");
    const selectorSummary = document.getElementById("station-selector-summary");
    const stationOptions = Array.from(document.querySelectorAll(".station-selector-option"));
    const selectAllButton = document.getElementById("station-select-all");
    const clearAllButton = document.getElementById("station-clear-all");
    const armButton = document.getElementById("arm-station-alerts");
    const testButton = document.getElementById("test-station-alert");
    const armStatus = document.getElementById("station-alert-arm-status");
    const message = document.getElementById("station-alert-message");
    const overlay = document.getElementById("dispatch-alert-overlay");
    const acknowledgeButton = document.getElementById("acknowledge-station-alert");
    const initialDataElement = document.getElementById("station-alert-data");

    let selectedStations = [];
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
    let alertMap = null;
    let pollTimer = null;
    let firstSnapshotForStation = true;
    let seenEventIds = new Set();
    let pendingAlerts = [];

    function text(id, value) {
        const element = document.getElementById(id);
        if (element) {
            element.textContent = value || "—";
        }
    }

    function optionalText(id, value) {
        const element = document.getElementById(id);
        if (!element) {
            return;
        }

        const hasValue = Boolean(value && String(value).trim());
        element.textContent = hasValue ? value : "";
        const detail = element.closest("[data-alert-detail]");
        if (detail) {
            detail.classList.toggle("d-none", !hasValue);
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

    function normalizeStations(values) {
        const normalized = [];
        const seen = new Set();
        (Array.isArray(values) ? values : [values]).forEach(function (value) {
            const station = String(value || "").trim();
            const key = station.toLowerCase();
            if (!station || seen.has(key)) {
                return;
            }
            seen.add(key);
            normalized.push(station);
        });
        return normalized;
    }

    function monitoredStationLabel(values) {
        const stations = normalizeStations(values);
        if (!stations.length) {
            return "None selected";
        }
        if (stations.length <= 3) {
            return stations.join(", ");
        }
        return stations.slice(0, 2).join(", ") + " +" + (stations.length - 2) + " more";
    }

    function updateStationSelector() {
        const selectedKeys = new Set(selectedStations.map(function (station) {
            return station.toLowerCase();
        }));
        stationOptions.forEach(function (option) {
            option.checked = selectedKeys.has(option.value.toLowerCase());
        });

        if (selectorSummary) {
            selectorSummary.textContent = selectedStations.length
                ? monitoredStationLabel(selectedStations)
                : "Choose one or more CAD stations...";
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
                selectedStations.length
                    ? "No units are assigned to the selected CAD stations."
                    : "Choose one or more stations to view their units."
            ));
            return;
        }

        units.forEach(function (unit) {
            const row = document.createElement("div");
            row.className = "station-unit-row";

            const number = document.createElement("div");
            number.className = "unit-number-strong";
            number.textContent = unit.unit_number || "Unknown unit";
            if (unit.station) {
                const station = document.createElement("div");
                station.className = "text-info small";
                station.textContent = unit.station;
                number.appendChild(station);
            }

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
                selectedStations.length
                    ? "No active CAD assignments for the selected stations."
                    : "Choose one or more stations to begin monitoring."
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
            if ((alert.station_names || []).length) {
                const stations = document.createElement("div");
                stations.className = "text-secondary small";
                stations.textContent = alert.station_names.join(", ");
                incident.appendChild(stations);
            }

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

        const snapshotStations = normalizeStations(data.selected_stations || data.selected_station || []);
        text("station-name", monitoredStationLabel(snapshotStations));
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
                ? "Two-tone paging, attention beeps, and siren audio are enabled. Keep this page open and the computer volume turned up."
                : "“Test Full Alert” will enable browser audio and play the complete paging sequence.";
        }
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
            if (!audio) {
                return;
            }
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
                message.textContent = "Paging tones completed, but MAE could not speak: " + announcementError;
                updateArmedDisplay();
            } else {
                armStatus.dataset.audioState = "finalizing";
                armStatus.innerHTML = '<span class="status-dot"></span> FINALIZING MAE ANNOUNCEMENT';
            }
            return;
        }

        announcementReleasePending = false;
        const playback = announcementAudio.play();
        if (playback && typeof playback.catch === "function") {
            playback.catch(function (error) {
                if (soundArmed) {
                    message.textContent = "Paging tones completed, but MAE could not speak: " + error.message;
                }
                stopAnnouncement();
                updateArmedDisplay();
            });
        }
    }

    async function prepareAnnouncement(announcement) {
        const spokenText = String(announcement || "").trim();
        if (!spokenText || !soundArmed || !overlay.classList.contains("visible")) {
            updateArmedDisplay();
            return;
        }

        const cycle = announcementCycle;
        announcementRequest = new AbortController();
        armStatus.dataset.audioState = "generating";
        armStatus.innerHTML = '<span class="status-dot"></span> GENERATING MAE ANNOUNCEMENT';

        try {
            const response = await fetch("/api/voice/speech", {
                method: "POST",
                cache: "no-store",
                headers: {
                    "Accept": "audio/mpeg",
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({
                    text: spokenText,
                    voice: "",
                    speed: 1.0,
                    response_format: "mp3"
                }),
                signal: announcementRequest.signal
            });
            if (!response.ok) {
                throw new Error("local speech returned " + response.status);
            }

            const audioBlob = await response.blob();
            if (
                cycle !== announcementCycle ||
                !soundArmed ||
                !overlay.classList.contains("visible")
            ) {
                return;
            }

            announcementRequest = null;
            announcementAudioUrl = URL.createObjectURL(audioBlob);
            announcementAudio = new Audio(announcementAudioUrl);
            announcementAudio.preload = "auto";
            announcementAudio.volume = 1;
            announcementAudio.load();
            announcementAudio.addEventListener("play", function () {
                armStatus.dataset.audioState = "speaking";
                armStatus.innerHTML = '<span class="status-dot"></span> MAE ANNOUNCEMENT PLAYING';
            });
            announcementAudio.addEventListener("ended", function () {
                stopAnnouncement();
                updateArmedDisplay();
            });

            if (announcementReleasePending) {
                releasePreparedAnnouncement();
            } else {
                armStatus.dataset.audioState = "ready";
                armStatus.innerHTML = '<span class="status-dot"></span> MAE ANNOUNCEMENT READY';
            }
        } catch (error) {
            if (error.name !== "AbortError" && cycle === announcementCycle) {
                announcementRequest = null;
                announcementError = error.message;
                if (announcementReleasePending) {
                    releasePreparedAnnouncement();
                } else {
                    armStatus.dataset.audioState = "unavailable";
                    armStatus.innerHTML = '<span class="status-dot"></span> MAE ANNOUNCEMENT UNAVAILABLE';
                }
            }
        }
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
        let phase = 0;
        segments.forEach(function (segment) {
            const segmentSamples = Math.round(segment.duration * sampleRate);
            const fadeSamples = Math.min(Math.round(0.012 * sampleRate), Math.floor(segmentSamples / 4));
            const startFrequency = Number(segment.frequency || 0);
            const endFrequency = Number(
                segment.endFrequency === undefined
                    ? startFrequency
                    : segment.endFrequency
            );

            for (let localSample = 0; localSample < segmentSamples; localSample += 1) {
                let sampleValue = 0;
                if (startFrequency > 0) {
                    const fadeIn = fadeSamples ? Math.min(1, localSample / fadeSamples) : 1;
                    const fadeOut = fadeSamples
                        ? Math.min(1, (segmentSamples - localSample - 1) / fadeSamples)
                        : 1;
                    const envelope = Math.max(0, Math.min(fadeIn, fadeOut));
                    const progress = segmentSamples > 1
                        ? localSample / (segmentSamples - 1)
                        : 0;
                    const frequency = startFrequency +
                        (endFrequency - startFrequency) * progress;
                    phase += 2 * Math.PI * frequency / sampleRate;
                    sampleValue = Math.sin(phase) *
                        (segment.amplitude || 0.75) * envelope;
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
                const announcement = pendingAnnouncementText;
                pendingAnnouncementText = "";
                if (announcement) {
                    releasePreparedAnnouncement();
                } else {
                    armStatus.dataset.audioState = "ready";
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
            ? "Two-tone paging, attention beeps, and siren audio are enabled. Keep this page open and the computer volume turned up."
            : "Audio could not be enabled. Click the button again.";

        if (soundArmed && settings.confirm !== false) {
            playAudio(
                confirmationAudio,
                "The browser blocked the confirmation sound."
            );
        }
        return soundArmed;
    }

    function playDispatchTone(announcement) {
        if (!soundArmed) {
            return;
        }

        ensureAudioPlayers();
        stopTone();
        pendingAnnouncementText = String(announcement || "").trim();
        if (pendingAnnouncementText) {
            prepareAnnouncement(pendingAnnouncementText);
        }
        playAudio(
            dispatchAudio,
            "The browser blocked the station alert audio. Check the tab sound permission."
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
        const streetViewLink = document.getElementById("alert-street-view");
        const googleMapsLink = document.getElementById("alert-google-maps");
        const hasCoordinates = validCoordinate(alert.latitude, -90, 90) &&
            validCoordinate(alert.longitude, -180, 180) &&
            !(Number(alert.latitude) === 0 && Number(alert.longitude) === 0);
        const locationQuery = encodeURIComponent(alert.location || "");

        if (streetViewLink) {
            streetViewLink.href = hasCoordinates
                ? "https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=" +
                    Number(alert.latitude) + "," + Number(alert.longitude)
                : "https://www.google.com/maps/search/?api=1&query=" + locationQuery;
        }

        if (googleMapsLink) {
            googleMapsLink.href = hasCoordinates
                ? "https://www.google.com/maps/search/?api=1&query=" +
                    Number(alert.latitude) + "," + Number(alert.longitude)
                : "https://www.google.com/maps/search/?api=1&query=" + locationQuery;
        }

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
        text(
            "alert-station-name",
            monitoredStationLabel(alert.station_names || selectedStations) || "Selected Stations"
        );
        text("alert-incident-code", alert.incident_code || "CAD DISPATCH");
        text("alert-incident-title", alert.incident_description || "CAD Dispatch");
        text("alert-priority", alert.priority ? ("PRI " + alert.priority) : "PRI —");
        text("alert-location", alert.location || "Location unavailable");
        text("alert-units", (alert.unit_numbers || []).join(", ") || "Unit unavailable");
        text("alert-cfs-number", alert.cfs_number || "TEST ALERT");
        text("alert-dispatch-time", formatCadTime(alert.dispatch_datetime || new Date().toISOString()));
        optionalText("alert-alternate-location", alert.alternate_location);
        optionalText("alert-caller-report", alert.caller_report);
        optionalText("alert-response-plan", alert.response_plan);
        optionalText("alert-safety-notes", alert.safety_notes);

        const soundNotice = document.getElementById("alert-sound-notice");
        soundNotice.textContent = soundArmed
            ? (isTest ? "TEST MODE — two-tone, attention beeps, and siren enabled." : "")
            : "VISUAL ALERT ONLY — click Enable Loud Alerts for audio.";

        overlay.classList.add("visible");
        renderAlertMap(alert);
        if (soundArmed) {
            playDispatchTone(alert.announcement);
        }
    }

    function hideAlert() {
        overlay.classList.remove("visible");
        stopTone();
        if (alertMap) {
            alertMap.remove();
            alertMap = null;
        }
        if (pendingAlerts.length) {
            const nextAlert = pendingAlerts.shift();
            window.setTimeout(function () {
                showAlert(nextAlert, false);
            }, 250);
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

        const now = new Date();
        const stationNumber = String(selectedStations[0] || "100")
            .replace(/^(station|sta)\s*[-:#]?\s*/i, "")
            .trim();
        const testTime = String(now.getHours()).padStart(2, "0") +
            String(now.getMinutes()).padStart(2, "0");
        const demo = {
            incident_code: "STRUCT",
            incident_description: "TEST — Commercial Structure Fire",
            priority: "5",
            location: "911 Mark Spurlock Drive, Logan, WV 25601",
            alternate_location: "Also mapped as 28 1/2 Main Avenue, Logan",
            caller_report: "TEST CALL: Multiple callers report smoke and flames visible from the second floor. Building evacuation is in progress.",
            response_plan: "TEST RESPONSE: Establish command, complete a primary search, secure a water supply, and position the ladder on the address side.",
            safety_notes: "TEST INFORMATION: Possible occupants inside. Use caution around parked vehicles and the rear service area.",
            unit_numbers: ["TEST ENG 1", "TEST LAD 1", "TEST RESCUE 1", "TEST MEDIC 1", "TEST CHIEF 1"],
            station_names: selectedStations.length ? selectedStations : ["TEST STATION"],
            cfs_number: "TEST-CFS-STRUCTURE-FIRE",
            dispatch_datetime: new Date().toISOString(),
            announcement: "Station " + stationNumber +
                ", respond to 911 Mark Spurlock Drive for a test commercial structure fire. Time is " +
                testTime + ".",
            latitude: 37.8507803,
            longitude: -81.9975482
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
            if (overlay.classList.contains("visible")) {
                pendingAlerts.push.apply(pendingAlerts, newAlerts);
            } else {
                showAlert(newAlerts[0], false);
                pendingAlerts.push.apply(pendingAlerts, newAlerts.slice(1));
            }
        }
    }

    async function loadSnapshot() {
        if (!selectedStations.length) {
            renderSnapshot({
                connected: true,
                selected_station: "",
                selected_stations: [],
                generated_at: new Date().toISOString(),
                station_units: [],
                alerts: []
            });
            return;
        }

        try {
            const query = new URLSearchParams();
            selectedStations.forEach(function (station) {
                query.append("station", station);
            });
            const response = await fetch(
                "/api/operations/station-alerts?" + query.toString(),
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

    function chooseStations(stations) {
        selectedStations = normalizeStations(stations);
        localStorage.setItem(STORAGE_STATIONS, JSON.stringify(selectedStations));
        localStorage.removeItem(LEGACY_STORAGE_STATION);
        updateStationSelector();
        seenEventIds = new Set();
        pendingAlerts = [];
        firstSnapshotForStation = true;
        hideAlert();

        const url = new URL(window.location.href);
        url.searchParams.delete("station");
        selectedStations.forEach(function (station) {
            url.searchParams.append("station", station);
        });
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
    let storedStations = [];
    try {
        storedStations = JSON.parse(localStorage.getItem(STORAGE_STATIONS) || "[]");
    } catch (_error) {
        storedStations = [];
    }
    if (!storedStations.length) {
        const legacyStation = localStorage.getItem(LEGACY_STORAGE_STATION) || "";
        storedStations = legacyStation ? [legacyStation] : [];
    }

    const initialSelections = normalizeStations(
        (initialData.selected_stations || []).length
            ? initialData.selected_stations
            : (initialData.selected_station ? [initialData.selected_station] : storedStations)
    );
    const optionNames = new Map(stationOptions.map(function (option) {
        return [option.value.toLowerCase(), option.value];
    }));
    selectedStations = initialSelections
        .map(function (station) {
            return optionNames.get(station.toLowerCase());
        })
        .filter(Boolean);
    updateStationSelector();

    stationOptions.forEach(function (option) {
        option.addEventListener("change", function () {
            chooseStations(stationOptions.filter(function (candidate) {
                return candidate.checked;
            }).map(function (candidate) {
                return candidate.value;
            }));
        });
    });
    if (selectAllButton) {
        selectAllButton.addEventListener("click", function () {
            chooseStations(stationOptions.map(function (option) {
                return option.value;
            }));
        });
    }
    if (clearAllButton) {
        clearAllButton.addEventListener("click", function () {
            chooseStations([]);
        });
    }
    armButton.addEventListener("click", armSound);
    testButton.addEventListener("click", testAlert);
    acknowledgeButton.addEventListener("click", hideAlert);
    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && overlay.classList.contains("visible")) {
            hideAlert();
        }
    });

    renderSnapshot(initialData);
    const snapshotSelection = normalizeStations(
        initialData.selected_stations || initialData.selected_station || []
    );
    const selectionMatchesSnapshot =
        selectedStations.length === snapshotSelection.length &&
        selectedStations.every(function (station, index) {
            return station.toLowerCase() === String(snapshotSelection[index] || "").toLowerCase();
        });
    if (selectedStations.length && selectionMatchesSnapshot) {
        baselineOrAlert(initialData.alerts || []);
    } else if (selectedStations.length) {
        loadSnapshot();
    } else {
        text("station-name", "None selected");
    }
    startPolling();
})();
