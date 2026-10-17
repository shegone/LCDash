// Mindshare Radio Intelligence (/mindshare/radio).
//
// Admins browse machine-generated radio transcripts and play the audio. The
// backend enforces admin-only access (403 otherwise) and records every list
// and every playback in the audit log; this script renders honestly whatever
// the API says. Every failure surfaces the HTTP status and the server's
// `detail` string inline -- no alert()/confirm(). All server text goes in via
// textContent only (no HTML injection): transcripts are untrusted machine output.
//
// Layout: one tab per channel plus "All channels". Each tab is a feed of
// cards (an <ol>), not a table, so it reads the same on a phone. Each tab keeps
// its own keyset cursor and is fetched the first time it is opened.
//
// Audio: ONE shared Audio object serves every card. Its source is set only
// when a person presses Play on a card, so nothing is fetched (or audited)
// until then, and starting another card stops the current one.
//
// Auto-refresh: while the page is visible and nothing is playing, the active
// tab fetches its newest page every 30 s and prepends segments not yet shown.
// It never clears the list, moves the scroll position, or touches playback.
(function () {
    "use strict";

    const STATS_REFRESH_MS = 60000;
    const FEED_REFRESH_MS = 30000;
    const PAGE_SIZE = 50;
    const CHANNEL_PATTERN = /^[a-z0-9_]{1,32}$/;
    const AUDIO_ERROR_TEXT =
        "Audio could not be played. It may have expired, or your session ended.";

    const statsBox = document.getElementById("radio-stats");
    const statsError = document.getElementById("radio-stats-error");
    const form = document.getElementById("radio-filter-form");
    const directionSelect = document.getElementById("radio-filter-direction");
    const textInput = document.getElementById("radio-filter-q");
    const applyButton = document.getElementById("radio-filter-apply");
    const tablist = document.getElementById("radio-tablist");
    const panelsBox = document.getElementById("radio-panels");
    const updatedText = document.getElementById("radio-updated");
    const refreshButton = document.getElementById("radio-refresh-button");
    const playStatus = document.getElementById("radio-play-status");
    if (!statsBox || !statsError || !form || !directionSelect || !textInput ||
        !applyButton || !tablist || !panelsBox || !updatedText || !refreshButton ||
        !playStatus) {
        return;
    }

    // key ("" for all channels) -> tab state
    const tabs = new Map();
    const tabOrder = [];
    let activeKey = "";
    let statsTimer = null;
    let feedTimer = null;

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

    function formatClock(date) {
        const iso = date.toISOString();
        if (window.LCDashTime && typeof window.LCDashTime.formatCadDisplayTime === "function") {
            return window.LCDashTime.formatCadDisplayTime(iso, { timeOnly: true });
        }
        return date.toLocaleTimeString();
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

    function element(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    function icon(name) {
        const node = element("i", `bi ${name}`);
        node.setAttribute("aria-hidden", "true");
        return node;
    }

    // Small stable hash so an unknown channel always gets the same colour.
    function channelClass(channel) {
        if (channel === "law" || channel === "ems" || channel === "fire") {
            return `radio-chip-${channel}`;
        }
        let hash = 0;
        for (let i = 0; i < channel.length; i += 1) {
            hash = (hash * 31 + channel.charCodeAt(i)) >>> 0;
        }
        return `radio-chip-c${hash % 4}`;
    }

    // The scrollable ancestor (the page shell scrolls, not the window).
    function scrollParent(node) {
        let parent = node.parentElement;
        while (parent && parent !== document.body) {
            const overflow = window.getComputedStyle(parent).overflowY;
            if ((overflow === "auto" || overflow === "scroll") &&
                parent.scrollHeight > parent.clientHeight) {
                return parent;
            }
            parent = parent.parentElement;
        }
        return document.scrollingElement || document.documentElement;
    }

    // ------------------------------------------------------------------ audio

    // One shared Audio object. preload "none" plus no source until Play is
    // pressed means listing the feed never fetches or audits any audio.
    const player = new Audio();
    player.preload = "none";
    // {segmentId, controls, state}; state: loading | playing | paused
    let current = null;

    function audioActive() {
        return Boolean(current && (current.state === "loading" || current.state === "playing"));
    }

    function announce(text) {
        playStatus.textContent = text;
    }

    function paintControls(controls, state) {
        const names = {
            idle: ["bi-play-fill", "Play"],
            paused: ["bi-play-fill", "Play"],
            loading: ["bi-hourglass-split", "Loading..."],
            playing: ["bi-pause-fill", "Pause"],
        };
        const entry = names[state] || names.idle;
        controls.icon.className = `bi ${entry[0]}`;
        controls.label.textContent = entry[1];
        controls.button.classList.toggle("is-playing", state === "playing");
        controls.button.classList.toggle("is-loading", state === "loading");
        const verb = state === "playing" ? "Pause" : state === "loading" ? "Loading" : "Play";
        controls.button.setAttribute("aria-label", `${verb} ${controls.descriptor}`);
    }

    function clearNote(controls) {
        controls.note.hidden = true;
        controls.note.textContent = "";
    }

    function releaseCurrent() {
        if (current) {
            paintControls(current.controls, "idle");
            current = null;
        }
    }

    function failPlayback(controls) {
        if (current && current.controls === controls) current = null;
        paintControls(controls, "idle");
        showError(controls.note, AUDIO_ERROR_TEXT);
        announce(AUDIO_ERROR_TEXT);
    }

    function startPlayback(segment, controls) {
        if (current && current.controls !== controls) {
            player.pause();
            releaseCurrent();
        }
        clearNote(controls);
        current = { segmentId: segment.segment_id, controls: controls, state: "loading" };
        paintControls(controls, "loading");
        announce(`Loading ${controls.descriptor}`);
        // The only place a source is ever assigned: an explicit press.
        player.src = `/api/radio/segments/${encodeURIComponent(segment.segment_id)}/audio`;
        const started = player.play();
        if (started && typeof started.catch === "function") {
            started.catch(function (error) {
                // A newer press or pause interrupts an older play(); that is
                // not a failure of the audio.
                if (!current || current.controls !== controls) return;
                if (error && error.name === "AbortError") return;
                failPlayback(controls);
            });
        }
    }

    function onPlayPressed(segment, controls) {
        if (current && current.controls === controls) {
            if (current.state === "playing") {
                player.pause();
                return;
            }
            if (current.state === "loading") {
                // Cancel a load that has not started yet.
                player.pause();
                current.state = "paused";
                paintControls(controls, "paused");
                announce("Stopped");
                return;
            }
            if (current.state === "paused") {
                current.state = "loading";
                paintControls(controls, "loading");
                const resumed = player.play();
                if (resumed && typeof resumed.catch === "function") {
                    resumed.catch(function (error) {
                        if (!current || current.controls !== controls) return;
                        if (error && error.name === "AbortError") return;
                        failPlayback(controls);
                    });
                }
                return;
            }
        }
        startPlayback(segment, controls);
    }

    player.addEventListener("playing", function () {
        if (!current) return;
        current.state = "playing";
        paintControls(current.controls, "playing");
        announce(`Playing ${current.controls.descriptor}`);
    });
    player.addEventListener("waiting", function () {
        if (!current || current.state !== "playing") return;
        current.state = "loading";
        paintControls(current.controls, "loading");
    });
    player.addEventListener("pause", function () {
        // Only a pause of audio that was actually playing counts; the pause
        // issued while switching to another card must not repaint the new one.
        if (!current || current.state !== "playing" || player.ended) return;
        current.state = "paused";
        paintControls(current.controls, "paused");
        announce("Paused");
    });
    player.addEventListener("ended", function () {
        if (!current) return;
        releaseCurrent();
        announce("Finished");
    });
    player.addEventListener("error", function () {
        if (!current || !player.error) return;
        failPlayback(current.controls);
    });

    function buildAudioArea(segment, descriptor) {
        const area = element("div", "radio-item-audio");
        if (!segment.audio_available) {
            area.appendChild(element("span", "radio-audio-note", "Audio expired"));
            return area;
        }
        const button = element("button", "radio-play");
        button.type = "button";
        const iconNode = icon("bi-play-fill");
        const label = element("span", "radio-play-label", "Play");
        button.appendChild(iconNode);
        button.appendChild(label);
        const note = element("span", "radio-audio-note is-error");
        note.hidden = true;
        const controls = {
            button: button, icon: iconNode, label: label, note: note, descriptor: descriptor,
        };
        paintControls(controls, "idle");
        button.addEventListener("click", function () {
            onPlayPressed(segment, controls);
        });
        area.appendChild(button);
        area.appendChild(note);
        return area;
    }

    // ------------------------------------------------------------------ cards

    function renderItem(segment) {
        const channel = String(segment.channel);
        const direction = String(segment.direction).toUpperCase();
        const time = formatTime(segment.started_at);
        const item = element("li", "radio-item");
        item.dataset.segmentId = String(segment.segment_id);

        const meta = element("div", "radio-item-meta");
        meta.appendChild(element("span", "radio-item-time", time));
        meta.appendChild(element("span", `radio-chip ${channelClass(channel)}`, channel));
        const dir = element("span", "radio-dir", direction);
        dir.title = direction === "TX" ? "Transmitted" : "Received";
        meta.appendChild(dir);
        const length = formatDuration(segment.duration_seconds);
        if (length) meta.appendChild(element("span", "radio-length", length));
        if (segment.low_confidence) {
            const badge = element("span", "radio-badge", "Low confidence");
            badge.title = "The transcriber was unsure about part of this segment.";
            meta.appendChild(badge);
        }
        item.appendChild(meta);

        const body = element("div", "radio-item-body");
        const text = element("p", "radio-item-text");
        if (segment.transcript) {
            text.textContent = segment.transcript;
        } else {
            text.classList.add("is-empty");
            text.textContent = "(no speech recognized)";
        }
        body.appendChild(text);
        body.appendChild(buildAudioArea(
            segment, `${channel} ${direction} segment from ${time}`));
        item.appendChild(body);
        return item;
    }

    // ------------------------------------------------------------------- tabs

    function tabLabel(channel) {
        return channel.toUpperCase();
    }

    function buildPanel(tab) {
        const panel = element("div", "radio-panel");
        panel.id = tab.panelId;
        panel.setAttribute("role", "tabpanel");
        panel.setAttribute("aria-labelledby", tab.tabId);
        panel.tabIndex = 0;
        panel.hidden = true;

        const loading = element("div", "admin-progress");
        loading.hidden = true;
        const spinner = element("div", "admin-progress-spinner");
        spinner.setAttribute("aria-hidden", "true");
        const loadingText = element("div");
        loadingText.appendChild(element("strong", "", "Loading segments…"));
        loading.appendChild(spinner);
        loading.appendChild(loadingText);

        const error = element("div", "admin-error");
        error.setAttribute("role", "alert");
        error.hidden = true;

        const empty = element("p", "admin-empty",
            "No segments match. Segments appear here after the listener finishes transcribing them.");
        empty.hidden = true;

        const list = element("ol", "radio-feed");
        list.setAttribute("aria-label",
            tab.channel ? `${tabLabel(tab.channel)} segments, newest first`
                        : "All channels segments, newest first");

        const more = element("div", "radio-more");
        const older = element("button", "");
        older.type = "button";
        older.hidden = true;
        older.appendChild(icon("bi-arrow-down-circle"));
        older.appendChild(document.createTextNode(" Load older"));
        const status = element("p", "radio-more-status");
        status.setAttribute("aria-live", "polite");
        more.appendChild(older);
        more.appendChild(status);

        panel.appendChild(loading);
        panel.appendChild(error);
        panel.appendChild(empty);
        panel.appendChild(list);
        panel.appendChild(more);
        panelsBox.appendChild(panel);

        older.addEventListener("click", function () {
            loadOlder(tab);
        });
        tab.panel = panel;
        tab.loading = loading;
        tab.error = error;
        tab.empty = empty;
        tab.list = list;
        tab.older = older;
        tab.status = status;
    }

    function registerTab(button) {
        const channel = button.dataset.channel || "";
        const key = channel;
        const tab = {
            key: key,
            channel: channel,
            button: button,
            countNode: button.querySelector(".radio-tab-count"),
            labelNode: button.querySelector(".radio-tab-label"),
            tabId: button.id,
            panelId: button.getAttribute("aria-controls"),
            loaded: false,
            busy: false,
            token: 0,
            nextBefore: "",
            seen: new Set(),
            updatedAt: 0,
            count: null,
        };
        buildPanel(tab);
        tabs.set(key, tab);
        tabOrder.push(key);
        button.addEventListener("click", function () {
            activateTab(key, false);
        });
        return tab;
    }

    function ensureChannelTab(channel) {
        if (tabs.has(channel) || !CHANNEL_PATTERN.test(channel)) return;
        const button = element("button", "radio-tab");
        button.type = "button";
        button.id = `radio-tab-${channel}`;
        button.dataset.channel = channel;
        button.setAttribute("role", "tab");
        button.setAttribute("aria-selected", "false");
        button.setAttribute("aria-controls", `radio-panel-${channel}`);
        button.tabIndex = -1;
        button.appendChild(element("span", "radio-tab-label", tabLabel(channel)));
        const count = element("span", "radio-tab-count");
        count.setAttribute("aria-hidden", "true");
        button.appendChild(count);
        tablist.appendChild(button);
        registerTab(button);
    }

    function setTabCount(tab, value) {
        tab.count = value;
        const label = tab.labelNode ? tab.labelNode.textContent : "";
        if (value === null) {
            tab.countNode.textContent = "";
            tab.button.removeAttribute("aria-label");
            return;
        }
        tab.countNode.textContent = String(value);
        tab.button.setAttribute("aria-label",
            `${label}, ${value} segment${value === 1 ? "" : "s"} in the last 24 hours`);
    }

    function activateTab(key, focus) {
        const tab = tabs.get(key);
        if (!tab) return;
        activeKey = key;
        tabs.forEach(function (other) {
            const selected = other.key === key;
            other.button.setAttribute("aria-selected", selected ? "true" : "false");
            other.button.tabIndex = selected ? 0 : -1;
            other.panel.hidden = !selected;
        });
        if (focus) tab.button.focus();
        showUpdated(tab);
        if (!tab.loaded) {
            loadFirst(tab);
        } else if (Date.now() - tab.updatedAt > FEED_REFRESH_MS) {
            refreshNewest(tab);
        }
    }

    tablist.addEventListener("keydown", function (event) {
        const index = tabOrder.indexOf(activeKey);
        let next = -1;
        if (event.key === "ArrowRight") next = (index + 1) % tabOrder.length;
        else if (event.key === "ArrowLeft") next = (index - 1 + tabOrder.length) % tabOrder.length;
        else if (event.key === "Home") next = 0;
        else if (event.key === "End") next = tabOrder.length - 1;
        if (next < 0) return;
        event.preventDefault();
        activateTab(tabOrder[next], true);
    });

    function showUpdated(tab) {
        updatedText.textContent = tab.updatedAt
            ? `Updated ${formatClock(new Date(tab.updatedAt))}`
            : "Not updated yet";
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
        const counts = new Map();
        let total = 0;
        channels.forEach(function (entry) {
            const name = String(entry.channel);
            const count = Number(entry.count) || 0;
            statsBox.appendChild(statTile(String(count), `${name}, last 24 h`, false));
            ensureChannelTab(name);
            counts.set(name, count);
            total += count;
        });
        const last = stats.last_received_at;
        statsBox.appendChild(statTile(
            last ? `${formatTime(last)} (${formatAge(last)})` : "Nothing received yet",
            "Last segment received",
            true));
        tabs.forEach(function (tab) {
            if (tab.key === "") {
                setTabCount(tab, Number.isFinite(Number(stats.total)) ? Number(stats.total) : total);
            } else {
                setTabCount(tab, counts.has(tab.key) ? counts.get(tab.key) : 0);
            }
        });
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
        if (feedTimer !== null) {
            window.clearInterval(feedTimer);
            feedTimer = null;
        }
        if (current) player.pause();
    }

    // ------------------------------------------------------------------ feeds

    function currentQuery(tab, before) {
        const params = new URLSearchParams();
        if (tab.channel) params.set("channel", tab.channel);
        if (directionSelect.value) params.set("direction", directionSelect.value);
        const q = textInput.value.trim();
        if (q) params.set("q", q);
        if (before) params.set("before", before);
        params.set("limit", String(PAGE_SIZE));
        return params.toString();
    }

    // Resolves {ended: true} when the session has ended, {error: text} for a
    // failure, or {payload} on success.
    async function requestSegments(tab, before) {
        try {
            const response = await fetch(
                `/api/radio/segments?${currentQuery(tab, before)}`,
                { headers: { "Accept": "application/json" } });
            if (window.LCDashSession && window.LCDashSession.check(response)) {
                return { ended: true };
            }
            if (!response.ok) {
                const detail = await readDetail(response);
                return {
                    error: `Segments could not be loaded${httpSuffix(response.status, detail)}`,
                };
            }
            return { payload: await response.json() };
        } catch (error) {
            return { error: "Segments could not be loaded (network error)" };
        }
    }

    function pageSegments(payload) {
        return Array.isArray(payload.segments) ? payload.segments : [];
    }

    function updateSummary(tab) {
        const shown = tab.list.children.length;
        tab.empty.hidden = shown > 0;
        tab.older.hidden = !tab.nextBefore;
        tab.status.textContent = shown
            ? `${shown} segment${shown === 1 ? "" : "s"} shown${tab.nextBefore ? "" : " (end of list)"}.`
            : "";
    }

    function appendItems(tab, segments, fresh) {
        const fragment = document.createDocumentFragment();
        segments.forEach(function (segment) {
            const id = String(segment.segment_id);
            if (tab.seen.has(id)) return;
            tab.seen.add(id);
            const item = renderItem(segment);
            if (fresh) item.classList.add("is-new");
            fragment.appendChild(item);
        });
        tab.list.appendChild(fragment);
    }

    function clearTab(tab) {
        tab.token += 1;
        tab.busy = false;
        tab.loaded = false;
        tab.nextBefore = "";
        tab.updatedAt = 0;
        tab.seen = new Set();
        tab.list.textContent = "";
        tab.error.hidden = true;
        tab.empty.hidden = true;
        tab.older.hidden = true;
        tab.status.textContent = "";
        tab.loading.hidden = true;
    }

    async function loadFirst(tab) {
        clearTab(tab);
        tab.busy = true;
        const token = tab.token;
        tab.loading.hidden = false;
        refreshButton.disabled = true;
        const result = await requestSegments(tab, "");
        if (token !== tab.token) return;
        tab.busy = false;
        tab.loading.hidden = true;
        refreshButton.disabled = false;
        if (result.ended) return;
        if (result.error) {
            showError(tab.error, result.error);
            return;
        }
        appendItems(tab, pageSegments(result.payload), false);
        tab.nextBefore = result.payload.has_more && result.payload.next_before
            ? result.payload.next_before : "";
        tab.loaded = true;
        tab.updatedAt = Date.now();
        updateSummary(tab);
        if (tab.key === activeKey) showUpdated(tab);
    }

    async function loadOlder(tab) {
        if (tab.busy || !tab.nextBefore) return;
        tab.busy = true;
        const token = tab.token;
        tab.older.disabled = true;
        tab.error.hidden = true;
        tab.status.textContent = "Loading older segments…";
        const result = await requestSegments(tab, tab.nextBefore);
        if (token !== tab.token) return;
        tab.busy = false;
        tab.older.disabled = false;
        if (result.ended) return;
        if (result.error) {
            tab.status.textContent = "";
            showError(tab.error, result.error);
            // Keep the older-button usable so a retry loses nothing shown.
            return;
        }
        appendItems(tab, pageSegments(result.payload), false);
        tab.nextBefore = result.payload.has_more && result.payload.next_before
            ? result.payload.next_before : "";
        updateSummary(tab);
    }

    // Fetch the newest page and prepend what is not on screen yet, keeping the
    // reader exactly where they are. Does not touch audio.
    async function refreshNewest(tab) {
        if (tab.busy) return;
        if (!tab.loaded) {
            loadFirst(tab);
            return;
        }
        tab.busy = true;
        const token = tab.token;
        refreshButton.disabled = true;
        const result = await requestSegments(tab, "");
        if (token !== tab.token) return;
        tab.busy = false;
        refreshButton.disabled = false;
        if (result.ended) return;
        if (result.error) {
            showError(tab.error, result.error);
            return;
        }
        tab.error.hidden = true;
        const segments = pageSegments(result.payload);
        const fresh = segments.filter(function (segment) {
            return !tab.seen.has(String(segment.segment_id));
        });
        const hadItems = tab.list.children.length > 0;
        if (hadItems && fresh.length === segments.length && segments.length > 0 &&
            result.payload.has_more) {
            // A whole page of unseen segments means older unseen ones may sit
            // between this page and what is on screen (the page was left
            // alone for a long time). Rebuild from the newest page rather
            // than leave a silent gap.
            loadFirst(tab);
            return;
        }
        if (fresh.length) {
            const anchor = tab.list.firstElementChild;
            const scroller = scrollParent(tab.list);
            const before = anchor ? anchor.getBoundingClientRect().top : 0;
            const fragment = document.createDocumentFragment();
            fresh.forEach(function (segment) {
                tab.seen.add(String(segment.segment_id));
                const item = renderItem(segment);
                item.classList.add("is-new");
                fragment.appendChild(item);
            });
            tab.list.insertBefore(fragment, tab.list.firstChild);
            if (anchor && scroller.scrollTop > 0) {
                // Keep what the reader is looking at where it was.
                scroller.scrollTop += anchor.getBoundingClientRect().top - before;
            }
        }
        tab.updatedAt = Date.now();
        updateSummary(tab);
        if (tab.key === activeKey) showUpdated(tab);
    }

    function autoRefresh() {
        if (document.visibilityState !== "visible") return;
        if (audioActive()) return;
        const tab = tabs.get(activeKey);
        if (tab) refreshNewest(tab);
    }

    function applyFilters() {
        tabs.forEach(function (tab) {
            if (tab.key !== activeKey) clearTab(tab);
        });
        const tab = tabs.get(activeKey);
        if (tab) loadFirst(tab);
    }

    form.addEventListener("submit", function (event) {
        event.preventDefault();
        applyFilters();
    });
    refreshButton.addEventListener("click", function () {
        const tab = tabs.get(activeKey);
        if (tab) refreshNewest(tab);
        loadStats();
    });
    document.addEventListener("visibilitychange", function () {
        if (document.visibilityState !== "visible") return;
        const tab = tabs.get(activeKey);
        if (tab && tab.loaded && Date.now() - tab.updatedAt > FEED_REFRESH_MS) autoRefresh();
    });

    // ------------------------------------------------------------------- init

    Array.from(tablist.querySelectorAll('[role="tab"]')).forEach(registerTab);
    activateTab("", false);
    loadStats();
    statsTimer = window.setInterval(loadStats, STATS_REFRESH_MS);
    feedTimer = window.setInterval(autoRefresh, FEED_REFRESH_MS);
    if (window.LCDashSession) {
        window.LCDashSession.onEnd(stopPolling);
    }
})();
