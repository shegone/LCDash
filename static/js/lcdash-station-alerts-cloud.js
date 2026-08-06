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

    let selectedStations = [];
    let firstSnapshot = true;
    let seenEventIds = new Set();

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

    function emptyState(message) {
        const element = document.createElement("div");
        element.className = "command-unavailable-state p-3 mt-3";
        element.textContent = message;
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

    function showVisualAlert(alert) {
        setText("alert-station-name", stationLabel(alert.station_names || selectedStations), "Selected stations");
        setText("alert-incident-code", alert.incident_code, "ASSIGNMENT EVENT");
        setText("alert-incident-title", alert.incident_description, "Assignment event");
        setText("alert-location", alert.location, "Location unavailable");
        setText("alert-units", (alert.unit_numbers || []).join(", "), "Unit unavailable");
        setText("alert-cfs-number", alert.cfs_number, "Unavailable");
        setText("alert-dispatch-time", formatTime(alert.dispatch_datetime));
        overlay.classList.add("visible");
    }

    function detectNewAssignments(alerts) {
        const incoming = Array.isArray(alerts) ? alerts : [];
        if (firstSnapshot) {
            incoming.forEach(function (alert) { if (alert.event_id) seenEventIds.add(alert.event_id); });
            firstSnapshot = false;
            return;
        }
        const next = incoming.find(function (alert) {
            return alert.event_id && !seenEventIds.has(alert.event_id);
        });
        incoming.forEach(function (alert) { if (alert.event_id) seenEventIds.add(alert.event_id); });
        if (next) showVisualAlert(next);
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
        overlay.classList.remove("visible");
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
