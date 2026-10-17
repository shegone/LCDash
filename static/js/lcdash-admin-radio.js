// Mindshare Radio Intelligence (/mindshare/radio).
//
// Admins browse machine-generated radio transcripts and play the audio. The
// backend enforces admin-only access (403 otherwise) and records every list
// and every playback in the audit log; this script renders honestly whatever
// the API says. Every failure surfaces the HTTP status and the server's
// `detail` string inline -- no alert()/confirm(). All server text goes in via
// textContent only (no HTML injection): transcripts are untrusted machine output.
//
// Audio elements use preload="none" and set their source up front, so nothing
// is fetched (or audited) until a person presses play.
(function () {
    "use strict";

    const STATS_REFRESH_MS = 60000;
    const PAGE_SIZE = 50;

    const statsBox = document.getElementById("radio-stats");
    const statsError = document.getElementById("radio-stats-error");
    const form = document.getElementById("radio-filter-form");
    const channelSelect = document.getElementById("radio-filter-channel");
    const directionSelect = document.getElementById("radio-filter-direction");
    const textInput = document.getElementById("radio-filter-q");
    const applyButton = document.getElementById("radio-filter-apply");
    const loading = document.getElementById("radio-loading");
    const errorBox = document.getElementById("radio-error");
    const emptyBox = document.getElementById("radio-empty");
    const tableWrap = document.getElementById("radio-table-wrap");
    const tbody = document.getElementById("radio-tbody");
    const olderButton = document.getElementById("radio-load-older");
    const moreStatus = document.getElementById("radio-more-status");
    if (!statsBox || !statsError || !form || !channelSelect || !directionSelect ||
        !textInput || !applyButton || !loading || !errorBox || !emptyBox ||
        !tableWrap || !tbody || !olderButton || !moreStatus) {
        return;
    }

    let nextBefore = "";
    let busy = false;
    let statsTimer = null;
    // Bumped on every fresh query so a slow response for an old filter can
    // never overwrite the table for the current one.
    let queryToken = 0;

    // ---------------------------------------------------------------- helpers

    function httpSuffix(status, detail) {
        return status ? ` (HTTP ${status}${detail ? `: ${detail}` : ""})` : " (network error)";
    }

    async function readDetail(response) {
        try {
            const body = await response.json();
            return typeof body.detail === "string" ? body.detail : "";
        } catch (error) {
            return "";
        }
    }

    function formatTime(value) {
        if (!value) return "";
        if (window.LCDashTime && typeof window.LCDashTime.formatCadDisplayTime === "function") {
            return window.LCDashTime.formatCadDisplayTime(value);
        }
        return String(value);
    }

    function formatDuration(seconds) {
        const n = Number(seconds);
        if (!isFinite(n) || n < 0) return "";
        if (n < 60) return `${n.toFixed(1)} s`;
        const minutes = Math.floor(n / 60);
        return `${minutes} min ${Math.round(n - minutes * 60)} s`;
    }

    function formatAge(value) {
        const then = Date.parse(value);
        if (!isFinite(then)) return "";
        const seconds = Math.max(0, Math.round((Date.now() - then) / 1000));
        if (seconds < 90) return "just now";
        const minutes = Math.round(seconds / 60);
        if (minutes < 90) return `${minutes} min ago`;
        const hours = Math.round(minutes / 60);
        if (hours < 48) return `${hours} h ago`;
        return `${Math.round(hours / 24)} days ago`;
    }

    function showError(element, text) {
        element.hidden = false;
        element.textContent = text;
    }

    // ------------------------------------------------------------------ stats

    function statTile(value, label, wide) {
        const tile = document.createElement("div");
        tile.className = `radio-stat${wide ? " is-wide" : ""}`;
        const strong = document.createElement("strong");
        strong.textContent = value;
        const span = document.createElement("span");
        span.textContent = label;
        tile.appendChild(strong);
        tile.appendChild(span);
        return tile;
    }

    function renderStats(stats) {
        statsBox.textContent = "";
        const channels = Array.isArray(stats.channels) ? stats.channels : [];
        if (!channels.length) {
            statsBox.appendChild(statTile("0", "segments, last 24 h", false));
        }
        channels.forEach(function (entry) {
            statsBox.appendChild(statTile(String(entry.count), `${entry.channel}, last 24 h`, false));
            ensureChannelOption(String(entry.channel));
        });
        const last = stats.last_received_at;
        statsBox.appendChild(statTile(
            last ? `${formatTime(last)} (${formatAge(last)})` : "Nothing received yet",
            "Last segment received",
            true));
    }

    function ensureChannelOption(channel) {
        const exists = Array.from(channelSelect.options).some(function (option) {
            return option.value === channel;
        });
        if (exists || !/^[a-z0-9_]{1,32}$/.test(channel)) return;
        const option = document.createElement("option");
        option.value = channel;
        option.textContent = channel;
        channelSelect.appendChild(option);
    }

    async function loadStats() {
        try {
            const response = await fetch("/api/radio/stats", {
                headers: { "Accept": "application/json" },
            });
            // A poll bounced to the identity provider must stop, not retry
            // (static/js/lcdash-session.js).
            if (window.LCDashSession && window.LCDashSession.check(response)) return;
            if (!response.ok) {
                const detail = await readDetail(response);
                showError(statsError,
                    `Statistics could not be loaded${httpSuffix(response.status, detail)}`);
                return;
            }
            statsError.hidden = true;
            renderStats(await response.json());
        } catch (error) {
            showError(statsError, "Statistics could not be loaded (network error)");
        }
    }

    function stopPolling() {
        if (statsTimer !== null) {
            window.clearInterval(statsTimer);
            statsTimer = null;
        }
    }

    // ------------------------------------------------------------------ table

    function audioCell(segment) {
        const cell = document.createElement("td");
        if (!segment.audio_available) {
            const note = document.createElement("span");
            note.className = "radio-audio-note";
            note.textContent = "Audio expired";
            cell.appendChild(note);
            return cell;
        }
        const player = document.createElement("audio");
        player.controls = true;
        player.preload = "none";
        player.setAttribute("aria-label",
            `Play ${segment.channel} ${segment.direction} segment from ${formatTime(segment.started_at)}`);
        player.src = `/api/radio/segments/${encodeURIComponent(segment.segment_id)}/audio`;
        const note = document.createElement("div");
        note.className = "radio-audio-note is-error";
        note.setAttribute("role", "status");
        note.hidden = true;
        player.addEventListener("error", function () {
            note.hidden = false;
            note.textContent =
                "Audio could not be played. It may have expired, or your session ended.";
        });
        player.addEventListener("play", function () {
            note.hidden = true;
        });
        cell.appendChild(player);
        cell.appendChild(note);
        return cell;
    }

    function renderRow(segment) {
        const row = document.createElement("tr");

        const timeCell = row.insertCell();
        timeCell.className = "radio-cell-time";
        timeCell.textContent = formatTime(segment.started_at);

        const channelCell = row.insertCell();
        const chip = document.createElement("span");
        chip.className = "radio-chip";
        chip.textContent = String(segment.channel);
        channelCell.appendChild(chip);

        const directionCell = row.insertCell();
        directionCell.textContent = String(segment.direction);

        const lengthCell = row.insertCell();
        lengthCell.className = "radio-cell-length";
        lengthCell.textContent = formatDuration(segment.duration_seconds);

        const transcriptCell = row.insertCell();
        transcriptCell.className = "radio-cell-transcript";
        const text = document.createElement("span");
        if (segment.transcript) {
            text.textContent = segment.transcript;
        } else {
            text.className = "is-empty";
            text.textContent = "(no speech recognized)";
        }
        transcriptCell.appendChild(text);
        if (segment.low_confidence) {
            const badge = document.createElement("span");
            badge.className = "radio-badge";
            badge.textContent = "Low confidence";
            badge.title = "The transcriber was unsure about part of this segment.";
            transcriptCell.appendChild(badge);
        }

        row.appendChild(audioCell(segment));
        return row;
    }

    function currentQuery(before) {
        const params = new URLSearchParams();
        if (channelSelect.value) params.set("channel", channelSelect.value);
        if (directionSelect.value) params.set("direction", directionSelect.value);
        const q = textInput.value.trim();
        if (q) params.set("q", q);
        if (before) params.set("before", before);
        params.set("limit", String(PAGE_SIZE));
        return params.toString();
    }

    function setBusy(value) {
        busy = value;
        applyButton.disabled = value;
        olderButton.disabled = value;
    }

    async function loadSegments(reset) {
        if (busy) return;
        setBusy(true);
        const token = reset ? ++queryToken : queryToken;
        errorBox.hidden = true;
        moreStatus.textContent = "";
        if (reset) {
            loading.hidden = false;
            emptyBox.hidden = true;
            tableWrap.hidden = true;
            olderButton.hidden = true;
            tbody.textContent = "";
            nextBefore = "";
        } else {
            moreStatus.textContent = "Loading older segments…";
        }
        try {
            const response = await fetch(
                `/api/radio/segments?${currentQuery(reset ? "" : nextBefore)}`,
                { headers: { "Accept": "application/json" } });
            if (window.LCDashSession && window.LCDashSession.check(response)) return;
            if (token !== queryToken) return;
            loading.hidden = true;
            if (!response.ok) {
                const detail = await readDetail(response);
                moreStatus.textContent = "";
                showError(errorBox,
                    `Segments could not be loaded${httpSuffix(response.status, detail)}`);
                // Keep the older-button usable after a failed page so the
                // admin can retry without losing the rows already shown.
                olderButton.hidden = reset || !nextBefore;
                return;
            }
            const payload = await response.json();
            const segments = Array.isArray(payload.segments) ? payload.segments : [];
            segments.forEach(function (segment) {
                tbody.appendChild(renderRow(segment));
            });
            nextBefore = payload.has_more && payload.next_before ? payload.next_before : "";
            olderButton.hidden = !nextBefore;
            const hasRows = tbody.children.length > 0;
            tableWrap.hidden = !hasRows;
            emptyBox.hidden = hasRows;
            moreStatus.textContent = hasRows
                ? `${tbody.children.length} segment${tbody.children.length === 1 ? "" : "s"} shown${nextBefore ? "" : " (end of list)"}.`
                : "";
        } catch (error) {
            loading.hidden = true;
            moreStatus.textContent = "";
            showError(errorBox, "Segments could not be loaded (network error)");
        } finally {
            setBusy(false);
        }
    }

    form.addEventListener("submit", function (event) {
        event.preventDefault();
        loadSegments(true);
    });
    olderButton.addEventListener("click", function () {
        loadSegments(false);
    });

    loadStats();
    loadSegments(true);
    statsTimer = window.setInterval(loadStats, STATS_REFRESH_MS);
    if (window.LCDashSession) {
        window.LCDashSession.onEnd(stopPolling);
    }
})();
