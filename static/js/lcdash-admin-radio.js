// Mindshare Radio Intelligence (/mindshare/radio).
//
// Listener health: GET /api/radio/stats feeds the health panel (24 h counts,
// the Listener tile, per-channel capture lines, queue depths, and a warning
// for audio the listener cannot decode). Polled every 15 s while the tab is
// visible.
//
// Every signed-in human role browses machine-generated radio transcripts,
// plays the audio, and may correct a transcript. Administrators also manage
// alert keywords, transcript fixes and hint words. The backend enforces who may do what
// (403 otherwise) and records every list, playback, correction and admin
// change in the audit log; this script renders honestly whatever the API says.
// Every failure surfaces the HTTP status and the server's `detail` string
// inline -- no alert()/confirm(). All server text goes in via textContent
// only (no HTML injection): transcripts are untrusted machine or human text.
//
// Layout: one tab per channel group (Law, EMS, Fire, Other) plus "All
// channels", and (administrators only) "Alert keywords", "Transcript fixes",
// "Hint words" and "Units". Chips under a group tab narrow it to one channel.
// Each feed tab is a list of cards (an <ol>), not a table, so it reads the same
// on a phone. Each tab keeps its own keyset cursor and is fetched the first
// time it is opened. A group tab asks the server for all of its channels at
// once (`channel=a,b,c`).
//
// Corrections: each card has an Edit button that opens an inline editor. A
// saved correction updates the card in place (its Play button and playback are
// left alone). A corrected card can show the machine text and can be reverted
// to it (inline two-step confirm).
//
// Audio: ONE shared Audio object serves every card. Its source is set only
// when a person presses Play on a card, so nothing is fetched (or audited)
// until then, and starting another card stops the current one.
//
// Who is talking: each card carries a chip with the radio ID and, when an
// administrator has named it, the unit name ("Medic 4 · 98426"); a console
// stream shows "Console: Position2". The chip is a button that filters every
// tab to that radio ID (server side, `radio_id`), with a bar to clear it.
// Administrators get a "Units" tab to name radio IDs; names show on every
// card for everyone.
//
// Auto-refresh: while the page is visible and nothing is playing, the active
// feed tab fetches its newest page every 30 s and prepends segments not yet
// shown. It never clears the list, moves the scroll position, touches
// playback, or overwrites a card that is being edited.
(function () {
    "use strict";

    const STATS_REFRESH_MS = 15000;
    const MAX_HINT = 60;
    const MAX_UNIT_NAME = 60;
    const RADIO_ID_PATTERN = /^[0-9]{1,10}$/;
    const TALKGROUP_PATTERN = /^G[0-9]{1,6}$/;
    const CONSOLE_LABEL_PATTERN = /^[A-Za-z0-9][A-Za-z0-9 _.-]{0,31}$/;
    const MIN_HINT = 2;
    const FEED_REFRESH_MS = 30000;
    // Scanner mode (auto-play). Every feed fetch is audited, so this polls every
    // 10 s rather than faster. A call older than SCAN_MAX_AGE_MS when it shows
    // up is not played (it would be stale news), and at most SCAN_MAX_QUEUE calls
    // wait their turn; the oldest are dropped first.
    const SCAN_REFRESH_MS = 10000;
    const SCAN_MAX_AGE_MS = 180000;
    const SCAN_MAX_QUEUE = 10;
    const PAGE_SIZE = 50;
    const MAX_CORRECTION = 20000;
    const CHANNEL_PATTERN = /^[a-z0-9_]{1,32}$/;
    // The feed tabs are channel groups. A channel belongs to the group its
    // name starts with ("law", "law_ward_vhf" -> Law); any other name is Other.
    const GROUPS = [
        { key: "law", label: "Law" },
        { key: "ems", label: "EMS" },
        { key: "fire", label: "Fire" },
        { key: "other", label: "Other" },
    ];
    // Listed in display order. `short` labels the chip inside a group; `full`
    // is the name shown on a card. A channel the listener adds before this list
    // is updated still works: it is shown under its own name.
    const CHANNEL_CATALOG = {
        law: { short: "SIRN", full: "Law SIRN" },
        law_ward_vhf: { short: "Ward VHF", full: "Law Ward VHF" },
        law_south_vhf: { short: "South VHF", full: "Law South VHF" },
        law_park_vhf: { short: "Park VHF", full: "Law Park VHF" },
        law_dmr: { short: "DMR", full: "Law DMR" },
        law_lmars: { short: "LMARS", full: "Law LMARS" },
        ems: { short: "SIRN", full: "EMS SIRN" },
        ems_ward_vhf: { short: "WardRock VHF", full: "EMS WardRock VHF" },
        ems_south_vhf: { short: "South VHF", full: "EMS South VHF" },
        ems_park_vhf: { short: "Park VHF", full: "EMS Park VHF" },
        ems_dmr: { short: "DMR", full: "EMS DMR" },
        ems_lmars: { short: "LMARS", full: "EMS LMARS" },
        fire: { short: "SIRN", full: "Fire SIRN" },
        fire_ward_vhf: { short: "WardRock VHF", full: "Fire WardRock VHF" },
        fire_south_vhf: { short: "South VHF", full: "Fire South VHF" },
        fire_park_vhf: { short: "Park VHF", full: "Fire Park VHF" },
        fire_north_vhf: { short: "North VHF", full: "Fire North VHF" },
        fire_horsepen_vhf: { short: "Horsepen VHF", full: "Fire Horsepen VHF" },
        fire_dmr: { short: "DMR", full: "Fire DMR" },
        fire_lmars: { short: "LMARS", full: "Fire LMARS" },
        sirn_multi_vhf: { short: "SIRN Multi VHF", full: "SIRN Multi VHF" },
    };
    // Known channels in display order; channels seen in the statistics join at the end.
    const knownChannels = new Set(Object.keys(CHANNEL_CATALOG));
    // channel -> segments in the last 24 hours, from the latest statistics.
    let channelCounts = new Map();

    function channelGroup(channel) {
        for (const group of GROUPS) {
            if (group.key !== "other" &&
                (channel === group.key || channel.startsWith(`${group.key}_`))) {
                return group.key;
            }
        }
        return "other";
    }

    function groupLabel(key) {
        const group = GROUPS.find(function (entry) {
            return entry.key === key;
        });
        return group ? group.label : key;
    }

    function groupChannels(key) {
        return Array.from(knownChannels).filter(function (channel) {
            return channelGroup(channel) === key;
        });
    }

    function channelName(channel) {
        const entry = CHANNEL_CATALOG[channel];
        return entry ? entry.full : channel;
    }

    function channelShort(channel) {
        const entry = CHANNEL_CATALOG[channel];
        return entry ? entry.short : channel;
    }

    const CONTROL_CHARS = /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/;
    const AUDIO_ERROR_TEXT =
        "Audio could not be played. It may have expired, or your session ended.";
    const KEEP_NOTE = "Corrected audio and text are kept for 1 year.";
    const NO_SPEECH = "(no speech recognized)";
    const NOISE_TITLE = "Very short or boilerplate text that the transcriber often " +
        "invents from background noise. Listen to confirm.";

    const statsBox = document.getElementById("radio-stats");
    const statsError = document.getElementById("radio-stats-error");
    const healthAnnounce = document.getElementById("radio-health-announce");
    const form = document.getElementById("radio-filter-form");
    const directionSelect = document.getElementById("radio-filter-direction");
    const textInput = document.getElementById("radio-filter-q");
    const applyButton = document.getElementById("radio-filter-apply");
    const tablist = document.getElementById("radio-tablist");
    const panelsBox = document.getElementById("radio-panels");
    const updatedText = document.getElementById("radio-updated");
    const refreshButton = document.getElementById("radio-refresh-button");
    const playStatus = document.getElementById("radio-play-status");
    const hideNoiseBox = document.getElementById("radio-hide-noise");
    const noiseCount = document.getElementById("radio-noise-count");
    const idFilterBar = document.getElementById("radio-id-filter");
    const districtBar = document.getElementById("radio-districts");
    const healthStatus = document.getElementById("radio-health-status");
    const healthStatusText = document.getElementById("radio-health-status-text");
    const healthToggle = document.getElementById("radio-health-toggle");
    const healthToggleWord = document.getElementById("radio-health-toggle-word");
    const healthBody = document.getElementById("radio-health-body");
    const scanBox = document.getElementById("radio-autoplay");
    const scanStatus = document.getElementById("radio-autoplay-status");
    if (!statsBox || !statsError || !form || !directionSelect || !textInput ||
        !applyButton || !tablist || !panelsBox || !updatedText || !refreshButton ||
        !playStatus) {
        return;
    }

    // key ("" for all channels, "admin:watch", "admin:fixes", "admin:hints") -> tab state
    const tabs = new Map();
    let activeKey = "";
    let statsTimer = null;
    let feedTimer = null;
    // The radio ID every feed is filtered to: {id, name} or null.
    let radioFilter = null;
    // Set when a unit name changed, so feeds reload the next time one is opened.
    let unitsDirty = false;

    // Per-card state, kept off the DOM so the DOM stays plain.
    const itemState = new WeakMap();
    let itemSeq = 0;

    // ---------------------------------------------------------------- helpers

    function httpSuffix(status, detail) {
        return status ? ` (HTTP ${status}${detail ? `: ${detail}` : ""})` : " (network error)";
    }

    async function readDetail(response) {
        try {
            const body = await response.json();
            if (typeof body.detail === "string") return body.detail;
            if (Array.isArray(body.detail)) {
                // Validation errors arrive as a list of {msg, loc}.
                return body.detail
                    .map(function (entry) {
                        return entry && typeof entry.msg === "string" ? entry.msg : "";
                    })
                    .filter(Boolean)
                    .join("; ");
            }
            return "";
        } catch (error) {
            return "";
        }
    }

    // One JSON request. Resolves {ended: true} when the session has ended,
    // {error: text} for a failure, or {payload} on success.
    async function apiCall(method, url, body, failurePrefix) {
        try {
            const options = { method: method, headers: { "Accept": "application/json" } };
            if (body !== undefined) {
                options.headers["Content-Type"] = "application/json";
                options.body = JSON.stringify(body);
            }
            const response = await fetch(url, options);
            // A call bounced to the identity provider must stop, not retry
            // (static/js/lcdash-session.js).
            if (window.LCDashSession && window.LCDashSession.check(response)) {
                return { ended: true };
            }
            if (!response.ok) {
                const detail = await readDetail(response);
                return { error: `${failurePrefix}${httpSuffix(response.status, detail)}` };
            }
            let payload = null;
            if (response.status !== 204) {
                try {
                    payload = await response.json();
                } catch (error) {
                    payload = null;
                }
            }
            return { payload: payload };
        } catch (error) {
            return { error: `${failurePrefix}${httpSuffix(0, "")}` };
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

    function showError(node, text) {
        node.hidden = false;
        node.textContent = text;
    }

    function hideError(node) {
        node.hidden = true;
        node.textContent = "";
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

    function iconButton(className, iconName, label) {
        const button = element("button", className);
        button.type = "button";
        button.appendChild(icon(iconName));
        button.appendChild(element("span", "", label));
        return button;
    }

    // Small stable hash so an unknown channel always gets the same colour.
    function channelClass(channel) {
        const group = channelGroup(channel);
        if (group !== "other") return `radio-chip-${group}`;
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

    function viewportTop(scroller) {
        if (scroller === document.scrollingElement || scroller === document.documentElement) {
            return 0;
        }
        return scroller.getBoundingClientRect().top;
    }

    function firstArray(payload, keys) {
        if (Array.isArray(payload)) return payload;
        if (payload && typeof payload === "object") {
            for (let i = 0; i < keys.length; i += 1) {
                if (Array.isArray(payload[keys[i]])) return payload[keys[i]];
            }
        }
        return [];
    }

    function unwrapSegment(payload) {
        if (!payload || typeof payload !== "object") return null;
        if (payload.segment_id) return payload;
        const inner = payload.segment || payload.item;
        return inner && inner.segment_id ? inner : null;
    }

    // ------------------------------------------------------------------ audio

    // One shared Audio object. preload "none" plus no source until Play is
    // pressed means listing the feed never fetches or audits any audio.
    const player = new Audio();
    player.preload = "none";
    // {segmentId, controls, state}; state: loading | playing | paused
    let current = null;
    // Scanner mode state (see "scanner mode" below). Off on every page load.
    let scanOn = false;
    let scanBlocked = false;
    let scanQueue = [];
    let scanSkipped = 0;
    let scanTimer = null;

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
        scanPump();
    }

    // `auto` is true only for scanner mode. The browser may refuse sound until
    // the person has clicked the page; that is not a failure of the audio, so
    // the call goes back to the front of the queue and the page asks for a click.
    function startPlayback(segment, controls, auto) {
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
                if (auto && error && error.name === "NotAllowedError") {
                    releaseCurrent();
                    scanQueue.unshift(segment);
                    scanBlocked = true;
                    scanPaint();
                    return;
                }
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
        scanPaint();
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
        scanPaint();
    });
    player.addEventListener("ended", function () {
        if (!current) return;
        releaseCurrent();
        announce("Finished");
        scanPump();
    });
    player.addEventListener("error", function () {
        if (!current || !player.error) return;
        failPlayback(current.controls);
    });

    // Returns {area, controls, available}; controls is null when there is no
    // audio. The area is kept across in-place card updates so a correction
    // never interrupts playback.
    function buildAudioArea(segment, descriptor) {
        const area = element("div", "radio-item-audio");
        if (!segment.audio_available) {
            area.appendChild(element("span", "radio-audio-note", "Audio expired"));
            return { area: area, controls: null, available: false };
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
        return { area: area, controls: controls, available: true };
    }

    // ------------------------------------------------------------------ cards

    function displayText(segment) {
        if (typeof segment.text === "string") return segment.text;
        return typeof segment.transcript === "string" ? segment.transcript : "";
    }

    function segmentSig(segment) {
        return [displayText(segment), segment.corrected_at || "", segment.corrected ? 1 : 0,
                segment.audio_available ? 1 : 0, segment.low_confidence ? 1 : 0,
                segment.possible_noise ? 1 : 0,
                segment.retained_until || "",
                segment.radio_id || "", segment.unit_name || "",
                segment.talkgroup || "", segment.console_label || ""].join("|");
    }

    // ------------------------------------------------------- possible noise
    //
    // The server flags transcripts that are probably invented from background
    // noise (`possible_noise`). The flag is display only: flagged cards get a
    // badge and dimmed text, and "Hide possible noise" hides them client-side
    // in every tab. Nothing is deleted. The choice is remembered for the
    // browser session; storage can be blocked, so every access is guarded.

    const HIDE_NOISE_KEY = "lcdash.radio.hideNoise";

    function readHideNoise() {
        try {
            return window.sessionStorage.getItem(HIDE_NOISE_KEY) === "1";
        } catch (error) {
            return false;
        }
    }

    function saveHideNoise(value) {
        try {
            window.sessionStorage.setItem(HIDE_NOISE_KEY, value ? "1" : "0");
        } catch (error) {
            // Not remembered; the checkbox still works for this page view.
        }
    }

    // "N hidden as possible noise" for the tab being shown, only while hiding.
    function updateNoiseCount() {
        if (!noiseCount) return;
        const tab = tabs.get(activeKey);
        let hidden = 0;
        if (tab && tab.kind === "feed" && hideNoiseBox && hideNoiseBox.checked) {
            hidden = tab.list.querySelectorAll(".radio-item.is-noise").length;
        }
        noiseCount.hidden = hidden === 0;
        noiseCount.textContent = hidden ? `${hidden} hidden as possible noise` : "";
    }

    function applyHideNoise() {
        panelsBox.classList.toggle("hide-noise", Boolean(hideNoiseBox && hideNoiseBox.checked));
        updateNoiseCount();
    }

    if (hideNoiseBox) {
        hideNoiseBox.checked = readHideNoise();
        applyHideNoise();
        hideNoiseBox.addEventListener("change", function () {
            saveHideNoise(hideNoiseBox.checked);
            applyHideNoise();
        });
    }

    function setItemError(item, text) {
        const state = itemState.get(item);
        if (!state || !state.error) return;
        if (text) showError(state.error, text);
        else hideError(state.error);
    }

    function focusTool(item, name) {
        const target = item.querySelector(`[data-tool="${name}"]`);
        if (target) target.focus({ preventScroll: true });
    }

    function toolButton(name, iconName, label) {
        const button = iconButton("radio-tool", iconName, label);
        button.dataset.tool = name;
        return button;
    }

    // The row of per-card actions. Rebuilt on its own (toggle, confirm) so the
    // text, audio and any open editor are never disturbed.
    function renderTools(item, focusName) {
        const state = itemState.get(item);
        const segment = state.segment;
        const row = element("div", "radio-item-tools");
        row.hidden = state.editing;

        const edit = toolButton("edit", "bi-pencil-square", "Edit");
        edit.setAttribute("aria-label", `Edit transcript of ${state.descriptor}`);
        edit.disabled = state.busy;
        edit.addEventListener("click", function () {
            openEditor(item);
        });
        row.appendChild(edit);

        if (segment.corrected) {
            const toggle = toolButton("toggle",
                state.showMachine ? "bi-eye-slash" : "bi-eye",
                state.showMachine ? "Hide machine text" : "Show machine text");
            toggle.setAttribute("aria-expanded", state.showMachine ? "true" : "false");
            toggle.setAttribute("aria-controls", state.machineId);
            toggle.addEventListener("click", function () {
                state.showMachine = !state.showMachine;
                state.machine.hidden = !state.showMachine;
                renderTools(item, "toggle");
            });
            row.appendChild(toggle);

            if (state.confirmRevert) {
                const confirmBox = element("span", "radio-confirm");
                confirmBox.setAttribute("role", "group");
                confirmBox.setAttribute("aria-label", "Confirm revert to machine text");
                confirmBox.appendChild(element("span", "radio-confirm-text",
                    "Replace the correction with the machine text? The correction stays in the history."));
                const yes = toolButton("revert-yes", "bi-arrow-counterclockwise", "Yes, revert");
                yes.classList.add("is-danger");
                yes.disabled = state.busy;
                yes.addEventListener("click", function () {
                    revertCorrection(item);
                });
                const no = toolButton("revert-no", "bi-x-lg", "Keep correction");
                no.disabled = state.busy;
                no.addEventListener("click", function () {
                    state.confirmRevert = false;
                    renderTools(item, "revert");
                });
                confirmBox.appendChild(yes);
                confirmBox.appendChild(no);
                row.appendChild(confirmBox);
            } else {
                const revert = toolButton("revert", "bi-arrow-counterclockwise",
                    "Revert to machine text");
                revert.disabled = state.busy;
                revert.addEventListener("click", function () {
                    state.confirmRevert = true;
                    setItemError(item, "");
                    renderTools(item, "revert-no");
                });
                row.appendChild(revert);
            }
        }

        if (state.tools && state.tools.parentNode) {
            state.tools.parentNode.replaceChild(row, state.tools);
        }
        state.tools = row;
        if (focusName) focusTool(item, focusName);
    }

    // ------------------------------------------------------- who is talking
    //
    // radio_id / talkgroup / console_label / unit_name come from the server
    // (null for segments captured before this was recorded). Each is checked
    // against its pattern before it is shown, and an empty chip is never drawn.

    function segmentRadioId(segment) {
        const raw = segment.radio_id;
        if (raw === null || raw === undefined) return "";
        const id = String(raw);
        return RADIO_ID_PATTERN.test(id) ? id : "";
    }

    function segmentUnitName(segment) {
        const raw = segment.unit_name;
        if (typeof raw !== "string") return "";
        return raw.trim().slice(0, MAX_UNIT_NAME);
    }

    function whoLabel(id, name) {
        return name ? `${name} · ${id}` : `ID ${id}`;
    }

    function appendWho(meta, segment) {
        const id = segmentRadioId(segment);
        if (id) {
            const name = segmentUnitName(segment);
            const chip = element("button", `radio-who${name ? " is-named" : ""}`, whoLabel(id, name));
            chip.type = "button";
            chip.dataset.radioId = id;
            const describe = name ? `radio ID ${id} (${name})` : `radio ID ${id}`;
            chip.title = `Show only ${describe}`;
            chip.setAttribute("aria-label", `Show only ${describe}`);
            chip.addEventListener("click", function () {
                setRadioFilter(id, name);
            });
            meta.appendChild(chip);
        }
        const group = typeof segment.talkgroup === "string" ? segment.talkgroup : "";
        if (TALKGROUP_PATTERN.test(group)) {
            const tag = element("span", "radio-tg", group);
            tag.title = "Talkgroup";
            meta.appendChild(tag);
        }
        const label = typeof segment.console_label === "string" ? segment.console_label.trim() : "";
        if (label && CONSOLE_LABEL_PATTERN.test(label)) {
            const consoleTag = element("span", "radio-console", `Console: ${label}`);
            consoleTag.title = "Dispatch console position";
            meta.appendChild(consoleTag);
        }
    }

    // ----------------------------------------------------- radio ID filter

    function syncFilterBar() {
        if (!idFilterBar) return;
        const tab = tabs.get(activeKey);
        idFilterBar.hidden = !(radioFilter && tab && tab.kind === "feed");
    }

    function renderFilterBar() {
        if (!idFilterBar) return;
        idFilterBar.textContent = "";
        if (radioFilter) {
            const name = radioFilter.name ? ` (${radioFilter.name})` : "";
            idFilterBar.appendChild(icon("bi-funnel-fill"));
            idFilterBar.appendChild(element("span", "radio-id-filter-text",
                `Showing radio ID ${radioFilter.id}${name} - `));
            const clear = element("button", "radio-id-filter-clear", "clear");
            clear.type = "button";
            clear.setAttribute("aria-label", "Clear the radio ID filter");
            clear.addEventListener("click", function () {
                setRadioFilter(null, "");
                textInput.focus({ preventScroll: true });
            });
            idFilterBar.appendChild(clear);
        }
        syncFilterBar();
    }

    // The unit name may be added or changed after the filter was set.
    function noteFilterName(segment) {
        if (!radioFilter || segmentRadioId(segment) !== radioFilter.id) return;
        const name = segmentUnitName(segment);
        if (name === radioFilter.name) return;
        radioFilter.name = name;
        renderFilterBar();
    }

    // Reloading would throw away an unsaved correction, so refuse instead.
    function feedsBlockedByEdit() {
        if (!hasEditingCard()) return false;
        const active = tabs.get(activeKey);
        if (active && active.kind === "feed") {
            showError(active.error, "Save or cancel the open edit before changing the filter.");
        }
        return true;
    }

    function reloadFeeds() {
        scanReset();
        const active = tabs.get(activeKey);
        tabs.forEach(function (tab) {
            if (tab.kind === "feed" && tab.key !== activeKey) clearTab(tab);
        });
        if (active && active.kind === "feed") loadFirst(active);
    }

    function setRadioFilter(id, name) {
        if (feedsBlockedByEdit()) return;
        radioFilter = id ? { id: id, name: name || "" } : null;
        renderFilterBar();
        reloadFeeds();
        if (radioFilter && idFilterBar) {
            const clear = idFilterBar.querySelector("button");
            if (clear) clear.focus({ preventScroll: true });
        }
    }

    // Build (or rebuild in place) everything on a card except the audio area,
    // which is reused so a correction never interrupts playback.
    function fillItem(item, segment) {
        const state = itemState.get(item);
        state.segment = segment;
        state.sig = segmentSig(segment);
        const channel = String(segment.channel);
        const direction = String(segment.direction).toUpperCase();
        const time = formatTime(segment.started_at);
        const corrected = Boolean(segment.corrected);
        state.descriptor = `${channelName(channel)} ${direction} segment from ${time}`;
        if (!corrected) state.confirmRevert = false;

        let audio = state.audio;
        if (audio && audio.available !== Boolean(segment.audio_available)) {
            if (audio.controls && current && current.controls === audio.controls) {
                player.pause();
                releaseCurrent();
            }
            audio = null;
        }
        if (!audio) audio = buildAudioArea(segment, state.descriptor);
        state.audio = audio;

        item.textContent = "";
        item.classList.toggle("is-corrected", corrected);

        const meta = element("div", "radio-item-meta");
        meta.appendChild(element("span", "radio-item-time", time));
        meta.appendChild(element("span", `radio-chip ${channelClass(channel)}`, channelName(channel)));
        const dir = element("span", "radio-dir", direction);
        dir.title = direction === "TX" ? "Transmitted" : "Received";
        meta.appendChild(dir);
        appendWho(meta, segment);
        noteFilterName(segment);
        const length = formatDuration(segment.duration_seconds);
        if (length) meta.appendChild(element("span", "radio-length", length));
        if (corrected) {
            const who = segment.corrected_by ? String(segment.corrected_by) : "a user";
            const when = formatTime(segment.corrected_at);
            const badge = element("span", "radio-badge is-corrected",
                `Corrected by ${who}${when ? `, ${when}` : ""}`);
            badge.title = "A person edited this transcript. The machine text is kept.";
            meta.appendChild(badge);
        } else if (segment.low_confidence) {
            const badge = element("span", "radio-badge", "Low confidence");
            badge.title = "The transcriber was unsure about part of this segment.";
            meta.appendChild(badge);
        }
        // Display only; the server never sets this on a corrected segment.
        const noise = !corrected && Boolean(segment.possible_noise);
        item.classList.toggle("is-noise", noise);
        if (noise) {
            const badge = element("span", "radio-badge is-noise", "Possible noise");
            badge.title = NOISE_TITLE;
            meta.appendChild(badge);
        }
        item.appendChild(meta);

        const body = element("div", "radio-item-body");
        const shown = displayText(segment);
        const text = element("p", "radio-item-text");
        if (shown) {
            text.textContent = shown;
        } else {
            text.classList.add("is-empty");
            text.textContent = NO_SPEECH;
        }
        text.hidden = state.editing;
        state.textNode = text;
        body.appendChild(text);
        body.appendChild(audio.area);
        item.appendChild(body);

        state.tools = element("div");
        item.appendChild(state.tools);
        state.machineId = `radio-machine-${state.seq}`;

        const machine = element("div", "radio-machine");
        machine.id = state.machineId;
        machine.appendChild(element("span", "radio-machine-label", "Machine text"));
        const machineText = element("p", "radio-machine-text");
        if (segment.machine_transcript) {
            machineText.textContent = String(segment.machine_transcript);
        } else {
            machineText.classList.add("is-empty");
            machineText.textContent = NO_SPEECH;
        }
        machine.appendChild(machineText);
        machine.hidden = !(corrected && state.showMachine);
        state.machine = machine;
        if (corrected) {
            item.appendChild(machine);
            const keep = element("p", "radio-keep-note", KEEP_NOTE);
            if (segment.retained_until) {
                keep.textContent = `${KEEP_NOTE} Kept until ${formatTime(segment.retained_until)}.`;
            }
            item.appendChild(keep);
        }

        const error = element("div", "radio-inline-error");
        error.setAttribute("role", "alert");
        error.hidden = true;
        state.error = error;
        item.appendChild(error);

        renderTools(item);
        if (state.editing && state.editor) {
            item.insertBefore(state.editor, error);
        }
    }

    function buildItem(segment) {
        const item = element("li", "radio-item");
        item.dataset.segmentId = String(segment.segment_id);
        itemSeq += 1;
        itemState.set(item, {
            seq: itemSeq, segment: null, sig: "", descriptor: "", showMachine: false,
            editing: false, editor: null, confirmRevert: false, busy: false,
            audio: null, tools: null, textNode: null, machine: null, machineId: "",
            error: null,
        });
        fillItem(item, segment);
        return item;
    }

    // Apply a new version of a segment to every card showing it, except one
    // being edited, keeping the reader's scroll position.
    function refreshItemInPlace(item, segment) {
        const state = itemState.get(item);
        if (!state || state.editing || state.busy) return;
        const scroller = scrollParent(item);
        const rect = item.getBoundingClientRect();
        const wasAbove = rect.bottom <= viewportTop(scroller);
        const oldHeight = rect.height;
        fillItem(item, segment);
        if (wasAbove) {
            scroller.scrollTop += item.getBoundingClientRect().height - oldHeight;
        }
        updateNoiseCount();
    }

    function applySegmentEverywhere(segment, exceptItem) {
        const id = String(segment.segment_id);
        tabs.forEach(function (tab) {
            if (tab.kind !== "feed") return;
            const item = tab.items.get(id);
            if (item && item !== exceptItem) refreshItemInPlace(item, segment);
        });
    }

    // ----------------------------------------------------------- corrections

    function hasEditingCard() {
        return Boolean(panelsBox.querySelector(".radio-item.is-editing"));
    }

    function cardCount(length) {
        return `${length.toLocaleString("en-US")} / ${MAX_CORRECTION.toLocaleString("en-US")} characters`;
    }

    function closeEditor(item, restoreFocus) {
        const state = itemState.get(item);
        if (!state || !state.editing) return;
        state.editing = false;
        item.classList.remove("is-editing");
        if (state.editor && state.editor.parentNode) {
            state.editor.parentNode.removeChild(state.editor);
        }
        state.editor = null;
        if (state.textNode) state.textNode.hidden = false;
        if (state.tools) state.tools.hidden = false;
        if (restoreFocus) focusTool(item, "edit");
    }

    function openEditor(item) {
        const state = itemState.get(item);
        if (!state || state.editing || state.busy) return;
        state.editing = true;
        state.confirmRevert = false;
        item.classList.add("is-editing");
        setItemError(item, "");
        renderTools(item);
        if (state.textNode) state.textNode.hidden = true;

        const initial = displayText(state.segment);
        const editor = element("form", "radio-editor");
        editor.noValidate = true;
        editor.setAttribute("aria-label", `Edit transcript of ${state.descriptor}`);

        const labelId = `radio-edit-label-${state.seq}`;
        const label = element("label", "radio-editor-label", "Corrected transcript");
        label.id = labelId;
        const area = element("textarea", "radio-editor-input");
        area.id = `radio-edit-${state.seq}`;
        label.htmlFor = area.id;
        area.maxLength = MAX_CORRECTION;
        area.value = initial;
        area.rows = Math.max(3, Math.min(12, Math.ceil(initial.length / 60) + 1));
        area.spellcheck = true;

        const count = element("span", "radio-editor-count", cardCount(initial.length));
        const hint = element("span", "radio-editor-hint", "Ctrl+Enter saves. Esc cancels.");
        const meta = element("div", "radio-editor-meta");
        meta.appendChild(count);
        meta.appendChild(hint);

        const save = iconButton("radio-save", "bi-check-lg", "Save");
        save.type = "submit";
        const cancel = iconButton("radio-cancel", "bi-x-lg", "Cancel");
        const actions = element("div", "radio-editor-actions");
        actions.appendChild(save);
        actions.appendChild(cancel);

        const error = element("div", "radio-inline-error");
        error.setAttribute("role", "alert");
        error.hidden = true;

        editor.appendChild(label);
        editor.appendChild(area);
        editor.appendChild(meta);
        editor.appendChild(element("p", "radio-keep-note", KEEP_NOTE));
        editor.appendChild(error);
        editor.appendChild(actions);

        area.addEventListener("input", function () {
            count.textContent = cardCount(area.value.length);
            count.classList.toggle("is-near-limit", area.value.length >= MAX_CORRECTION - 500);
        });
        cancel.addEventListener("click", function () {
            if (state.busy) return;
            closeEditor(item, true);
        });
        area.addEventListener("keydown", function (event) {
            if (event.key === "Escape") {
                event.preventDefault();
                event.stopPropagation();
                if (!state.busy) closeEditor(item, true);
            } else if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
                event.preventDefault();
                editor.requestSubmit ? editor.requestSubmit() : save.click();
            }
        });
        editor.addEventListener("submit", function (event) {
            event.preventDefault();
            saveCorrection(item, { area: area, save: save, cancel: cancel, error: error });
        });

        state.editor = editor;
        item.insertBefore(editor, state.error);
        area.focus({ preventScroll: true });
        area.setSelectionRange(area.value.length, area.value.length);
    }

    async function saveCorrection(item, controls) {
        const state = itemState.get(item);
        if (!state || state.busy) return;
        const text = controls.area.value.trim();
        hideError(controls.error);
        if (!text) {
            showError(controls.error,
                "Enter the corrected text. To go back to what the machine heard, cancel and use Revert to machine text.");
            controls.area.focus({ preventScroll: true });
            return;
        }
        if (text.length > MAX_CORRECTION) {
            showError(controls.error,
                `The correction is ${text.length.toLocaleString("en-US")} characters; the limit is ${MAX_CORRECTION.toLocaleString("en-US")}.`);
            return;
        }
        if (CONTROL_CHARS.test(text)) {
            showError(controls.error,
                "The correction contains control characters. Only plain text, line breaks and tabs are allowed.");
            return;
        }
        if (state.segment.corrected && text === displayText(state.segment)) {
            closeEditor(item, true);
            return;
        }

        state.busy = true;
        controls.save.disabled = true;
        controls.cancel.disabled = true;
        controls.area.readOnly = true;
        controls.save.querySelector("span").textContent = "Saving…";
        const result = await apiCall(
            "PUT",
            `/api/radio/segments/${encodeURIComponent(state.segment.segment_id)}/correction`,
            { text: text },
            "The correction could not be saved");
        state.busy = false;
        controls.save.disabled = false;
        controls.cancel.disabled = false;
        controls.area.readOnly = false;
        controls.save.querySelector("span").textContent = "Save";
        if (result.ended) return;
        if (result.error) {
            showError(controls.error, result.error);
            controls.area.focus({ preventScroll: true });
            return;
        }
        const updated = unwrapSegment(result.payload);
        closeEditor(item, false);
        if (!updated) {
            fillItem(item, state.segment);
            setItemError(item,
                "The correction was saved, but the server's reply was not understood. Use Refresh to see it.");
            focusTool(item, "edit");
            return;
        }
        fillItem(item, updated);
        applySegmentEverywhere(updated, item);
        item.classList.remove("is-saved");
        // Restart the highlight animation.
        void item.offsetWidth;
        item.classList.add("is-saved");
        focusTool(item, "edit");
        announce("Correction saved");
    }

    async function revertCorrection(item) {
        const state = itemState.get(item);
        if (!state || state.busy) return;
        state.busy = true;
        setItemError(item, "");
        renderTools(item);
        const result = await apiCall(
            "DELETE",
            `/api/radio/segments/${encodeURIComponent(state.segment.segment_id)}/correction`,
            undefined,
            "The correction could not be reverted");
        state.busy = false;
        if (result.ended) {
            renderTools(item);
            return;
        }
        if (result.error) {
            state.confirmRevert = false;
            renderTools(item, "revert");
            setItemError(item, result.error);
            return;
        }
        const updated = unwrapSegment(result.payload);
        state.confirmRevert = false;
        state.showMachine = false;
        if (!updated) {
            renderTools(item, "edit");
            setItemError(item,
                "The correction was reverted, but the server's reply was not understood. Use Refresh to see it.");
            return;
        }
        fillItem(item, updated);
        applySegmentEverywhere(updated, item);
        focusTool(item, "edit");
        announce("Reverted to machine text");
    }

    // ------------------------------------------------------------------- tabs

    // The name a feed's list is announced by, for screen readers.
    function feedLabel(tab) {
        if (!tab.group) return "All channels segments, newest first";
        const what = tab.selected
            ? channelName(tab.selected)
            : `${groupLabel(tab.group)} channels`;
        return `${what} segments, newest first`;
    }

    // What the server is asked for: the chosen channel, else every channel in
    // the group, else (the All tab) no filter.
    function tabChannelFilter(tab) {
        if (tab.selected) return tab.selected;
        if (!tab.group) return "";
        return groupChannels(tab.group).join(",");
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
        list.setAttribute("aria-label", feedLabel(tab));

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
        const group = button.dataset.group || "";
        const tab = {
            key: group,
            kind: "feed",
            group: group,
            // The single channel the group is narrowed to ("" for the whole group).
            selected: "",
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
            items: new Map(),
            updatedAt: 0,
            count: null,
        };
        buildPanel(tab);
        tabs.set(tab.key, tab);
        button.addEventListener("click", function () {
            activateTab(tab.key, false);
        });
        return tab;
    }

    // A channel the listener reports that this page does not list yet joins
    // its group (or Other) under its own name.
    function noteChannel(channel) {
        if (CHANNEL_PATTERN.test(channel)) knownChannels.add(channel);
    }

    // ----------------------------------------------- channel chips in a group

    // The chips under the tabs narrow a group to one channel. They are rebuilt
    // only when the group's channels change; the 15 s statistics refresh just
    // updates the counts, so a focused chip keeps focus.
    function renderDistricts() {
        if (!districtBar) return;
        const tab = tabs.get(activeKey);
        const members = tab && tab.kind === "feed" && tab.group ? groupChannels(tab.group) : [];
        if (members.length < 2) {
            districtBar.hidden = true;
            districtBar.textContent = "";
            districtBar.dataset.sig = "";
            return;
        }
        districtBar.hidden = false;
        const sig = `${tab.key}|${members.join(",")}`;
        if (districtBar.dataset.sig !== sig) {
            districtBar.dataset.sig = sig;
            districtBar.textContent = "";
            const entries = [["", `All ${groupLabel(tab.group)}`]].concat(
                members.map(function (channel) {
                    return [channel, channelShort(channel)];
                }));
            entries.forEach(function (entry) {
                const chip = element("button", "radio-district");
                chip.type = "button";
                chip.dataset.channel = entry[0];
                // The chip takes its group's colour (law blue, EMS green, fire red).
                chip.dataset.group = tab.group;
                chip.appendChild(element("span", "radio-district-label", entry[1]));
                chip.appendChild(element("span", "radio-district-count"));
                chip.addEventListener("click", function () {
                    selectDistrict(tab, entry[0]);
                });
                districtBar.appendChild(chip);
            });
        }
        let groupTotal = 0;
        members.forEach(function (channel) {
            groupTotal += channelCounts.get(channel) || 0;
        });
        Array.from(districtBar.children).forEach(function (chip) {
            const channel = chip.dataset.channel;
            const selected = channel === tab.selected;
            chip.setAttribute("aria-pressed", selected ? "true" : "false");
            const count = channel ? (channelCounts.get(channel) || 0) : groupTotal;
            chip.querySelector(".radio-district-count").textContent = String(count);
            chip.setAttribute("aria-label",
                `${channel ? channelName(channel) : chip.firstChild.textContent}, ` +
                `${count} segment${count === 1 ? "" : "s"} in the last 24 hours`);
        });
    }

    function selectDistrict(tab, channel) {
        if (tab.selected === channel) return;
        if (feedsBlockedByEdit()) return;
        tab.selected = channel;
        scanReset();
        tab.list.setAttribute("aria-label", feedLabel(tab));
        renderDistricts();
        const pressed = districtBar.querySelector('[aria-pressed="true"]');
        if (pressed) pressed.focus({ preventScroll: true });
        loadFirst(tab);
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

    function tabsInOrder() {
        const ordered = [];
        Array.from(tablist.children).forEach(function (button) {
            tabs.forEach(function (tab) {
                if (tab.button === button) ordered.push(tab);
            });
        });
        return ordered;
    }

    function activateTab(key, focus) {
        const tab = tabs.get(key);
        if (!tab) return;
        if (key !== activeKey) scanReset();
        activeKey = key;
        tabs.forEach(function (other) {
            const selected = other.key === key;
            other.button.setAttribute("aria-selected", selected ? "true" : "false");
            other.button.tabIndex = selected ? 0 : -1;
            other.panel.hidden = !selected;
        });
        // The filters only apply to the transcript feeds.
        form.hidden = tab.kind !== "feed";
        syncFilterBar();
        renderDistricts();
        updateNoiseCount();
        if (focus) tab.button.focus();
        showUpdated(tab);
        if (tab.kind === "admin") {
            tab.load();
            return;
        }
        if (unitsDirty && !hasEditingCard()) {
            // A unit name changed: cards shown earlier still carry the old name.
            unitsDirty = false;
            tabs.forEach(function (other) {
                if (other.kind === "feed") clearTab(other);
            });
        }
        if (!tab.loaded) {
            loadFirst(tab);
        } else if (Date.now() - tab.updatedAt > FEED_REFRESH_MS) {
            tab.scanQuiet = scanOn;
            refreshNewest(tab);
        }
    }

    tablist.addEventListener("keydown", function (event) {
        const ordered = tabsInOrder();
        const index = ordered.findIndex(function (tab) {
            return tab.key === activeKey;
        });
        let next = -1;
        if (event.key === "ArrowRight") next = (index + 1) % ordered.length;
        else if (event.key === "ArrowLeft") next = (index - 1 + ordered.length) % ordered.length;
        else if (event.key === "Home") next = 0;
        else if (event.key === "End") next = ordered.length - 1;
        if (next < 0) return;
        event.preventDefault();
        activateTab(ordered[next].key, true);
    });

    function showUpdated(tab) {
        updatedText.textContent = tab.updatedAt
            ? `Updated ${formatClock(new Date(tab.updatedAt))}`
            : "Not updated yet";
    }

    // ------------------------------------------------------------------ stats

    function statTile(value, label, wide, tone) {
        const tile = document.createElement("div");
        tile.className = `radio-stat${wide ? " is-wide" : ""}${tone ? ` is-${tone}` : ""}`;
        const strong = document.createElement("strong");
        strong.textContent = value;
        const span = document.createElement("span");
        span.textContent = label;
        tile.appendChild(strong);
        tile.appendChild(span);
        return tile;
    }

    function wholeNumber(value) {
        const n = Number(value);
        return value !== null && value !== undefined && isFinite(n) && n >= 0 ? Math.round(n) : null;
    }

    function formatCount(value) {
        const n = wholeNumber(value);
        return n === null ? "unknown" : n.toLocaleString("en-US");
    }

    // "12 s ago", "4 min ago", "3 h ago": exact enough for a heartbeat.
    function formatSecondsAgo(seconds) {
        const n = wholeNumber(seconds);
        if (n === null) return "";
        if (n < 60) return `${n} s ago`;
        if (n < 3600) return `${Math.round(n / 60)} min ago`;
        if (n < 172800) return `${Math.round(n / 3600)} h ago`;
        return `${Math.round(n / 86400)} days ago`;
    }

    function formatSpan(seconds) {
        const n = wholeNumber(seconds);
        if (n === null) return "unknown";
        if (n < 60) return `${n} s`;
        if (n < 3600) return `${Math.floor(n / 60)} min`;
        const days = Math.floor(n / 86400);
        const hours = Math.floor((n % 86400) / 3600);
        if (days > 0) return `${days} d ${hours} h`;
        return `${hours} h ${Math.floor((n % 3600) / 60)} min`;
    }

    function listenerName(row) {
        const channel = typeof row.channel === "string" ? row.channel.slice(0, 32) : "?";
        const direction = typeof row.direction === "string" ? row.direction.slice(0, 8) : "";
        return direction ? `${channel} ${direction}` : channel;
    }

    // Per-minute packet rate from the interval counters; null when unusable.
    function packetsPerMinute(row) {
        const packets = wholeNumber(row.packets_interval);
        const seconds = Number(row.interval_seconds);
        if (packets === null || !isFinite(seconds) || seconds <= 0) return null;
        return Math.round((packets * 60) / seconds);
    }

    function channelLine(row, reporting) {
        const name = listenerName(row);
        const perMinute = packetsPerMinute(row);
        if (perMinute === null) return `${name}: no figures yet`;
        if (perMinute === 0) {
            return reporting ? `${name}: no traffic (quiet is normal)` : `${name}: no traffic`;
        }
        return `${name}: ${formatCount(perMinute)} packet${perMinute === 1 ? "" : "s"} in the last minute`;
    }

    // Payload types known to be signaling, not voice. Type 103 was identified
    // on 2026-10-17 by a passive 180 s sample of the radio streams: every voice
    // packet is 160-byte PCMU, while type 103 packets are 8-27 bytes, arrive as
    // a 3-packet burst every 60 s on idle streams (keepalive) and around each
    // transmission (PTT / talker information). It carries no speech, so it must
    // not raise the "undecoded audio" warning. Any OTHER type still does.
    const KNOWN_SIGNALING_PAYLOAD_TYPES = new Set(["103"]);

    // Undecoded payload types across all listener rows:
    // [{type, packets, where: [names]}], sorted by payload type.
    function undecodedAudio(rows) {
        const byType = new Map();
        rows.forEach(function (row) {
            const other = row.other_payload_types;
            if (!other || typeof other !== "object") return;
            Object.keys(other).forEach(function (type) {
                if (KNOWN_SIGNALING_PAYLOAD_TYPES.has(String(type))) return;
                const packets = wholeNumber(other[type]);
                if (!packets) return;
                const label = String(type).slice(0, 8);
                if (!byType.has(label)) byType.set(label, { type: label, packets: 0, where: [] });
                const entry = byType.get(label);
                entry.packets += packets;
                const name = listenerName(row);
                if (entry.where.indexOf(name) < 0) entry.where.push(name);
            });
        });
        return Array.from(byType.values()).sort(function (x, y) {
            return Number(x.type) - Number(y.type) || (x.type < y.type ? -1 : 1);
        });
    }

    function healthCard(title) {
        const card = element("div", "radio-health-card");
        card.appendChild(element("h3", "radio-health-card-title", title));
        return card;
    }

    function healthLines(card, lines) {
        const list = element("ul", "radio-health-lines");
        lines.forEach(function (line) {
            list.appendChild(element("li", line.tone ? `is-${line.tone}` : "", line.text));
        });
        card.appendChild(list);
    }

    function listenerState(listener) {
        if (!listener || typeof listener !== "object") return "never";
        return listener.reporting ? "reporting" : "silent";
    }

    function renderListenerTile(listener, state) {
        if (state === "never") {
            return statTile("No heartbeat received yet", "Listener", true, "unknown");
        }
        if (state === "reporting") {
            const age = formatSecondsAgo(listener.age_seconds);
            return statTile(age ? `Reporting, ${age}` : "Reporting", "Listener", true, "ok");
        }
        const since = listener.last_heartbeat_at ? formatTime(listener.last_heartbeat_at) : "";
        const ago = formatSecondsAgo(listener.age_seconds);
        const value = since ? `Not reporting since ${since}` : "Not reporting";
        return statTile(ago && since ? `${value} (${ago})` : value, "Listener", true, "bad");
    }

    function renderWarning(undecoded) {
        const box = element("div", "radio-health-warning");
        box.dataset.health = "undecoded";
        box.appendChild(icon("bi-exclamation-triangle-fill"));
        const body = element("div", "radio-health-warning-body");
        undecoded.forEach(function (entry) {
            const noun = entry.packets === 1 ? "packet" : "packets";
            const block = element("p", "radio-health-warning-line");
            block.appendChild(element("strong", "",
                `Undecoded audio: payload type ${entry.type}, ${formatCount(entry.packets)} ${noun}.`));
            block.appendChild(document.createTextNode(
                " These transmissions are not being transcribed."));
            body.appendChild(block);
            body.appendChild(element("p", "radio-health-warning-where",
                `Seen on: ${entry.where.join(", ")}`));
        });
        box.appendChild(body);
        return box;
    }

    function renderDetail(listener, state) {
        const reporting = state === "reporting";
        const rows = Array.isArray(listener.listeners)
            ? listener.listeners.filter(function (row) {
                return row && typeof row === "object";
            })
            : [];
        const detail = element("div", "radio-health-detail");

        const channels = healthCard(reporting ? "Capture, per channel" : "Capture, as last reported");
        channels.dataset.health = "channels";
        healthLines(channels, rows.length
            ? rows.map(function (row) {
                return { text: channelLine(row, reporting) };
            })
            : [{ text: "No channels reported." }]);
        const uptime = wholeNumber(listener.capture_uptime_seconds);
        channels.appendChild(element("p", "radio-health-uptime",
            `Capture uptime: ${uptime === null ? "unknown" : formatSpan(uptime)}`));
        detail.appendChild(channels);

        const queues = healthCard("Queues");
        queues.dataset.health = "queues";
        const transcriber = listener.transcriber || {};
        const uploader = listener.uploader || {};
        const lines = [{
            text: `Transcriber: ${formatCount(transcriber.pending)} pending, ` +
                `${formatCount(transcriber.failed)} failed`,
            tone: wholeNumber(transcriber.failed) ? "warn" : "",
        }];
        const queued = wholeNumber(uploader.queued);
        const oldest = wholeNumber(uploader.oldest_queued_age_seconds);
        lines.push({
            text: `Uploader: ${formatCount(uploader.queued)} queued` +
                (oldest !== null && queued ? `, oldest ${formatSpan(oldest)}` : ""),
            tone: oldest !== null && oldest > 600 ? "warn" : "",
        });
        const rejected = wholeNumber(uploader.rejected_total);
        if (rejected) {
            lines.push({ text: `Uploader: ${formatCount(rejected)} rejected by the server`, tone: "warn" });
        }
        healthLines(queues, lines);
        detail.appendChild(queues);

        return detail;
    }

    // The panel re-renders every poll, so it is not a live region. Only a
    // change of state is announced.
    function announceHealth(state, undecoded) {
        if (!healthAnnounce) return;
        const key = state + (undecoded.length ? "+undecoded" : "");
        if (healthAnnounce.dataset.state === key) return;
        const first = healthAnnounce.dataset.state === undefined;
        healthAnnounce.dataset.state = key;
        // A healthy first load is not news.
        if (first && key === "reporting") return;
        const parts = [{
            reporting: "Listener is reporting.",
            silent: "Listener is not reporting.",
            never: "No listener heartbeat has been received yet.",
        }[state]];
        if (undecoded.length) parts.push("Warning: undecoded audio is not being transcribed.");
        healthAnnounce.textContent = parts.join(" ");
    }

    // The one-line status that stays on screen when the details are folded away.
    function setHealthSummary(tone, text) {
        if (!healthStatus || !healthStatusText) return;
        healthStatus.className = `radio-health-status is-${tone}`;
        healthStatus.dataset.state = tone;
        healthStatusText.textContent = text;
    }

    function healthSummary(listener, state, undecoded, last) {
        const parts = [];
        let tone = "unknown";
        if (state === "reporting") {
            const age = formatSecondsAgo(listener.age_seconds);
            parts.push(age ? `Listener reporting, ${age}` : "Listener reporting");
            tone = undecoded.length ? "warn" : "ok";
        } else if (state === "silent") {
            const since = listener.last_heartbeat_at ? formatTime(listener.last_heartbeat_at) : "";
            parts.push(since ? `Listener NOT reporting since ${since}` : "Listener NOT reporting");
            tone = "bad";
        } else {
            parts.push("No listener heartbeat received yet");
        }
        if (undecoded.length) parts.push("undecoded audio, see details");
        if (last) parts.push(`last segment ${formatAge(last)}`);
        setHealthSummary(tone, parts.join(" \u00b7 "));
    }

    function setHealthOpen(open) {
        if (!healthToggle || !healthBody) return;
        healthBody.hidden = !open;
        healthToggle.setAttribute("aria-expanded", open ? "true" : "false");
        if (healthToggleWord) healthToggleWord.textContent = open ? "Hide" : "Show";
        // A per-viewer convenience only; the page works without it.
        try {
            window.localStorage.setItem("lcdash.radio.health.open", open ? "1" : "0");
        } catch (error) { /* storage may be blocked */ }
    }

    if (healthToggle && healthBody) {
        let remembered = false;
        try {
            remembered = window.localStorage.getItem("lcdash.radio.health.open") === "1";
        } catch (error) { /* storage may be blocked */ }
        healthBody.hidden = !remembered;
        healthToggle.setAttribute("aria-expanded", remembered ? "true" : "false");
        if (healthToggleWord) healthToggleWord.textContent = remembered ? "Hide" : "Show";
        healthToggle.addEventListener("click", function () {
            setHealthOpen(healthBody.hidden);
        });
    }

    function renderStats(stats) {
        statsBox.textContent = "";
        statsBox.classList.remove("is-stale");
        const listener = stats.listener && typeof stats.listener === "object" ? stats.listener : null;
        const state = listenerState(listener);
        const rows = listener && Array.isArray(listener.listeners) ? listener.listeners : [];
        const undecoded = undecodedAudio(rows.filter(function (row) {
            return row && typeof row === "object";
        }));

        const tiles = element("div", "radio-health-tiles");
        tiles.dataset.health = "tiles";
        tiles.appendChild(renderListenerTile(listener, state));
        const channels = Array.isArray(stats.channels) ? stats.channels : [];
        if (!channels.length) {
            tiles.appendChild(statTile("0", "segments, last 24 h", false));
        }
        const counts = new Map();
        let total = 0;
        channels.forEach(function (entry) {
            const name = String(entry.channel);
            const count = Number(entry.count) || 0;
            tiles.appendChild(statTile(String(count), `${channelName(name)}, last 24 h`, false));
            noteChannel(name);
            counts.set(name, count);
            total += count;
        });
        channelCounts = counts;
        const last = stats.last_received_at;
        tiles.appendChild(statTile(
            last ? `${formatTime(last)} (${formatAge(last)})` : "Nothing received yet",
            "Last segment received",
            true));
        statsBox.appendChild(tiles);

        if (undecoded.length) statsBox.appendChild(renderWarning(undecoded));
        if (state !== "never") statsBox.appendChild(renderDetail(listener, state));
        healthSummary(listener, state, undecoded, last);
        announceHealth(state, undecoded);

        tabs.forEach(function (tab) {
            if (tab.kind !== "feed") return;
            if (tab.key === "") {
                setTabCount(tab, Number.isFinite(Number(stats.total)) ? Number(stats.total) : total);
            } else {
                let groupTotal = 0;
                counts.forEach(function (count, channel) {
                    if (channelGroup(channel) === tab.key) groupTotal += count;
                });
                setTabCount(tab, groupTotal);
            }
        });
        renderDistricts();
    }

    let statsBusy = false;

    async function loadStats() {
        if (statsBusy) return;
        statsBusy = true;
        let result;
        try {
            result = await apiCall("GET", "/api/radio/stats", undefined,
                "Statistics could not be loaded");
        } finally {
            statsBusy = false;
        }
        if (result.ended) return;
        if (result.error) {
            showError(statsError, result.error);
            // What is on screen is old: say so rather than leave a green tile up.
            statsBox.classList.add("is-stale");
            setHealthSummary("unknown", "Listener status could not be loaded");
            return;
        }
        hideError(statsError);
        renderStats(result.payload || {});
    }

    // Poll only while the page is visible; coming back refreshes at once.
    function pollStats() {
        if (document.visibilityState !== "visible") return;
        loadStats();
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
        scanSet(false);
        if (current) player.pause();
    }

    // ------------------------------------------------------------------ feeds

    function currentQuery(tab, before) {
        const params = new URLSearchParams();
        const channelFilter = tabChannelFilter(tab);
        if (channelFilter) params.set("channel", channelFilter);
        if (directionSelect.value) params.set("direction", directionSelect.value);
        const q = textInput.value.trim();
        if (q) params.set("q", q);
        if (radioFilter) params.set("radio_id", radioFilter.id);
        if (before) params.set("before", before);
        params.set("limit", String(PAGE_SIZE));
        return params.toString();
    }

    function requestSegments(tab, before) {
        return apiCall("GET", `/api/radio/segments?${currentQuery(tab, before)}`,
            undefined, "Segments could not be loaded");
    }

    function pageSegments(payload) {
        return payload && Array.isArray(payload.segments) ? payload.segments : [];
    }

    function updateSummary(tab) {
        const shown = tab.list.children.length;
        tab.empty.hidden = shown > 0;
        tab.older.hidden = !tab.nextBefore;
        tab.status.textContent = shown
            ? `${shown} segment${shown === 1 ? "" : "s"} shown${tab.nextBefore ? "" : " (end of list)"}.`
            : "";
        if (tab.key === activeKey) updateNoiseCount();
    }

    function addItem(tab, segment, fresh) {
        const id = String(segment.segment_id);
        tab.seen.add(id);
        const item = buildItem(segment);
        tab.items.set(id, item);
        if (fresh) item.classList.add("is-new");
        return item;
    }

    function appendItems(tab, segments, fresh) {
        const fragment = document.createDocumentFragment();
        segments.forEach(function (segment) {
            if (tab.seen.has(String(segment.segment_id))) return;
            fragment.appendChild(addItem(tab, segment, fresh));
        });
        tab.list.appendChild(fragment);
    }

    function clearTab(tab) {
        tab.scanQuiet = false;
        tab.token += 1;
        tab.busy = false;
        tab.loaded = false;
        tab.nextBefore = "";
        tab.updatedAt = 0;
        tab.seen = new Set();
        tab.items = new Map();
        tab.list.textContent = "";
        hideError(tab.error);
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
        hideError(tab.error);
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
    // reader exactly where they are. Cards already shown are updated in place
    // only when somebody else changed them and they are not being edited.
    // Does not touch audio.
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
        hideError(tab.error);
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
            // than leave a silent gap -- but never while a card is open for
            // editing: the draft is more important than the gap.
            if (!hasEditingCard()) {
                loadFirst(tab);
                return;
            }
        }
        segments.forEach(function (segment) {
            const item = tab.items.get(String(segment.segment_id));
            const state = item && itemState.get(item);
            if (state && !state.editing && !state.busy && state.sig !== segmentSig(segment)) {
                refreshItemInPlace(item, segment);
            }
        });
        if (fresh.length) {
            const anchor = tab.list.firstElementChild;
            const scroller = scrollParent(tab.list);
            const before = anchor ? anchor.getBoundingClientRect().top : 0;
            const fragment = document.createDocumentFragment();
            fresh.forEach(function (segment) {
                fragment.appendChild(addItem(tab, segment, true));
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
        // The first refresh after switching tabs, or after turning the switch
        // on, only catches the page up: what arrived while the person was not
        // listening is not played.
        if (fresh.length && !tab.scanQuiet) scanOffer(tab, fresh);
        tab.scanQuiet = false;
    }

    // ---------------------------------------------------------- scanner mode
    //
    // "Auto-play new calls": calls that arrive while the switch is on play one
    // after another, oldest first, for the tab (and channel chip) being watched.
    // Each card keeps its Play button, so a call can be replayed at any time.
    // It never plays what was already on screen, only new arrivals; it waits
    // behind anything a person is playing or has paused; it asks for a click
    // when the browser blocks sound; and it is off again on every page load.
    // Playing a call goes through the same path as the Play button, so every
    // listen is audited like any other.

    function scanItemControls(tab, segment) {
        const item = tab.items.get(String(segment.segment_id));
        const state = item && itemState.get(item);
        return state && state.audio ? state.audio.controls : null;
    }

    // Is this call recent enough to be worth playing now?
    function scanIsCurrent(segment) {
        const stamp = Date.parse(segment.ended_at || segment.started_at || "");
        return Number.isFinite(stamp) && Date.now() - stamp <= SCAN_MAX_AGE_MS;
    }

    function scanReset() {
        scanQueue = [];
        scanSkipped = 0;
        scanPaint();
    }

    function scanOffer(tab, fresh) {
        if (!scanOn || tab.key !== activeKey) return;
        // `fresh` is newest first; the queue plays oldest first.
        fresh.slice().reverse().forEach(function (segment) {
            if (!segment.audio_available) return;
            if (segment.possible_noise && hideNoiseBox && hideNoiseBox.checked) return;
            if (!scanIsCurrent(segment)) return;
            scanQueue.push(segment);
        });
        while (scanQueue.length > SCAN_MAX_QUEUE) {
            scanQueue.shift();
            scanSkipped += 1;
        }
        scanPump();
    }

    // Start the next waiting call, unless something is already loading, playing
    // or paused (a person's pause holds the scanner until they resume or stop).
    function scanPump() {
        if (!scanOn || scanBlocked || current) {
            scanPaint();
            return;
        }
        const tab = tabs.get(activeKey);
        while (tab && tab.kind === "feed" && scanQueue.length) {
            const segment = scanQueue.shift();
            const controls = scanItemControls(tab, segment);
            if (!controls) continue;
            startPlayback(segment, controls, true);
            break;
        }
        scanPaint();
    }

    function scanPaint() {
        if (!scanStatus) return;
        scanStatus.hidden = !scanOn;
        if (!scanOn) return;
        const waiting = scanQueue.length ? ` (${scanQueue.length} waiting)` : "";
        let text;
        if (scanBlocked) {
            text = "Click anywhere on the page to start playing";
        } else if (current && current.state === "paused") {
            text = `Paused${waiting}`;
        } else if (current) {
            text = `Playing now${waiting}`;
        } else {
            text = "Listening for new calls";
        }
        if (scanSkipped) text += `, ${scanSkipped} skipped`;
        scanStatus.textContent = text;
    }

    function scanTick() {
        const tab = tabs.get(activeKey);
        if (scanOn && tab && tab.kind === "feed") refreshNewest(tab);
    }

    function scanSet(on) {
        scanOn = on;
        scanBlocked = false;
        scanReset();
        if (scanBox && scanBox.checked !== on) scanBox.checked = on;
        if (scanTimer !== null) {
            window.clearInterval(scanTimer);
            scanTimer = null;
        }
        const watched = tabs.get(activeKey);
        if (on && watched) watched.scanQuiet = true;
        if (on) {
            // Unlike the 30 s refresh this keeps running while the page is hidden:
            // a scanner is often left on a second screen.
            scanTimer = window.setInterval(scanTick, SCAN_REFRESH_MS);
            scanTick();
        }
        scanPaint();
    }

    // A click or key press is the permission the browser wants before sound.
    function scanUnblock() {
        if (!scanBlocked) return;
        scanBlocked = false;
        scanPump();
    }
    ["pointerdown", "keydown", "click"].forEach(function (name) {
        document.addEventListener(name, scanUnblock, true);
    });

    if (scanBox) {
        scanBox.checked = false;
        scanBox.addEventListener("change", function () {
            scanSet(scanBox.checked);
        });
    }

    function autoRefresh() {
        if (document.visibilityState !== "visible") return;
        if (audioActive()) return;
        const tab = tabs.get(activeKey);
        if (tab && tab.kind === "feed") refreshNewest(tab);
    }

    function applyFilters() {
        if (feedsBlockedByEdit()) return;
        reloadFeeds();
    }

    form.addEventListener("submit", function (event) {
        event.preventDefault();
        applyFilters();
    });
    refreshButton.addEventListener("click", function () {
        const tab = tabs.get(activeKey);
        if (!tab) return;
        if (tab.kind === "admin") {
            tab.load();
            return;
        }
        refreshNewest(tab);
        loadStats();
    });
    document.addEventListener("visibilitychange", function () {
        if (document.visibilityState !== "visible") return;
        loadStats();
        const tab = tabs.get(activeKey);
        if (tab && tab.kind === "feed" && tab.loaded &&
            Date.now() - tab.updatedAt > FEED_REFRESH_MS) {
            autoRefresh();
        }
    });

    // ----------------------------------------------------- administrator tabs

    // Two-step delete: a plain Delete button turns into "Delete? Yes / Cancel".
    // `run` resolves true when the row is gone, false when it failed (it shows
    // its own error), so the control can return to its idle state.
    // `options` may reword it ("Clear" instead of "Delete").
    function makeDeleteControl(label, run, options) {
        const words = Object.assign({
            idle: "Delete", ask: "Delete this?", yes: "Yes, delete", icon: "bi-trash3",
        }, options || {});
        const wrap = element("span", "radio-delete");
        wrap.dataset.state = "idle";
        function idle(focusIt) {
            wrap.textContent = "";
            wrap.dataset.state = "idle";
            const button = iconButton("radio-tool is-danger", words.icon, words.idle);
            button.setAttribute("aria-label", `${words.idle} ${label}`);
            button.addEventListener("click", askConfirm);
            wrap.appendChild(button);
            if (focusIt) button.focus({ preventScroll: true });
        }
        function askConfirm() {
            wrap.textContent = "";
            wrap.dataset.state = "confirm";
            wrap.setAttribute("role", "group");
            wrap.appendChild(element("span", "radio-confirm-text", words.ask));
            const yes = iconButton("radio-tool is-danger", words.icon, words.yes);
            yes.setAttribute("aria-label", `${words.yes} ${label}`);
            const no = iconButton("radio-tool", "bi-x-lg", "Cancel");
            yes.addEventListener("click", async function () {
                yes.disabled = true;
                no.disabled = true;
                const gone = await run();
                if (!gone) idle(true);
            });
            no.addEventListener("click", function () {
                idle(true);
            });
            wrap.appendChild(yes);
            wrap.appendChild(no);
            no.focus({ preventScroll: true });
        }
        idle(false);
        return wrap;
    }

    function registerAdminTab(button) {
        const name = button.dataset.adminTab;
        const panel = document.getElementById(button.dataset.panel || "");
        if (!name || !panel) return null;
        button.setAttribute("role", "tab");
        button.setAttribute("aria-selected", "false");
        button.setAttribute("aria-controls", panel.id);
        button.tabIndex = -1;
        panel.setAttribute("role", "tabpanel");
        panel.setAttribute("aria-labelledby", button.id);
        panel.tabIndex = 0;
        panel.hidden = true;
        const tab = {
            key: `admin:${name}`,
            kind: "admin",
            name: name,
            button: button,
            panel: panel,
            loaded: false,
            busy: false,
            updatedAt: 0,
            load: function () {},
        };
        tabs.set(tab.key, tab);
        button.addEventListener("click", function () {
            activateTab(tab.key, false);
        });
        return tab;
    }

    function byId(id) {
        return document.getElementById(id);
    }

    function channelChips(channels) {
        const wrap = element("span", "radio-admin-channels");
        if (!channels.length) {
            wrap.appendChild(element("span", "radio-chip radio-chip-all", "All channels"));
            return wrap;
        }
        channels.forEach(function (channel) {
            const name = String(channel);
            wrap.appendChild(element("span", `radio-chip ${channelClass(name)}`, channelName(name)));
        });
        return wrap;
    }

    function rowError(row) {
        let node = row.querySelector(".radio-inline-error");
        if (!node) {
            node = element("div", "radio-inline-error");
            node.setAttribute("role", "alert");
            node.hidden = true;
            row.appendChild(node);
        }
        return node;
    }

    // ---- Alert keywords

    function normalizeTerm(raw) {
        if (!raw || typeof raw !== "object") return null;
        const id = raw.term_id !== undefined ? raw.term_id : raw.id;
        if (id === undefined || id === null || typeof raw.phrase !== "string") return null;
        return {
            id: id,
            phrase: raw.phrase,
            channels: Array.isArray(raw.channels) ? raw.channels.map(String) : [],
            enabled: raw.enabled !== false,
        };
    }

    function setupWatchTab(tab) {
        const list = byId("radio-watch-list");
        const empty = byId("radio-watch-empty");
        const loading = byId("radio-watch-loading");
        const error = byId("radio-watch-error");
        const addForm = byId("radio-watch-form");
        const phrase = byId("radio-watch-phrase");
        const formError = byId("radio-watch-form-error");
        const total = byId("radio-watch-total");
        const addButton = byId("radio-watch-add");
        if (!list || !empty || !loading || !error || !addForm || !phrase || !formError ||
            !addButton) {
            return;
        }
        // One checkbox per known channel, grouped, after the fixed "All channels".
        const channelBox = byId("radio-watch-channels");
        if (channelBox) {
            GROUPS.forEach(function (group) {
                groupChannels(group.key).forEach(function (channel) {
                    const label = element("label", "radio-check");
                    const box = element("input");
                    box.type = "checkbox";
                    box.name = "channel";
                    box.value = channel;
                    label.appendChild(box);
                    label.appendChild(document.createTextNode(` ${channelName(channel)}`));
                    channelBox.appendChild(label);
                });
            });
        }
        const boxes = Array.from(addForm.querySelectorAll('input[name="channel"]'));
        const allBox = boxes.find(function (box) {
            return box.value === "all";
        });

        function updateSummaryText() {
            const n = list.children.length;
            empty.hidden = n > 0;
            if (total) total.textContent = n ? `${n} alert keyword${n === 1 ? "" : "s"}` : "";
        }

        function renderTerm(term) {
            const row = element("li", "radio-admin-row");
            row.dataset.termId = String(term.id);
            const main = element("div", "radio-admin-main");
            main.appendChild(element("strong", "radio-admin-phrase", term.phrase));
            main.appendChild(channelChips(term.channels));

            const toggleLabel = element("label", "radio-switch");
            const toggle = element("input");
            toggle.type = "checkbox";
            toggle.checked = term.enabled;
            toggle.setAttribute("aria-label", `Enabled: ${term.phrase}`);
            const toggleText = element("span", "", term.enabled ? "Enabled" : "Disabled");
            toggleLabel.appendChild(toggle);
            toggleLabel.appendChild(toggleText);
            toggle.addEventListener("change", async function () {
                const wanted = toggle.checked;
                toggle.disabled = true;
                hideError(rowError(row));
                const result = await apiCall(
                    "PATCH", `/api/radio/watch-terms/${encodeURIComponent(term.id)}`,
                    { enabled: wanted }, "The keyword could not be changed");
                toggle.disabled = false;
                if (result.ended) return;
                if (result.error) {
                    toggle.checked = !wanted;
                    showError(rowError(row), result.error);
                    return;
                }
                toggleText.textContent = wanted ? "Enabled" : "Disabled";
            });

            const del = makeDeleteControl(`keyword ${term.phrase}`, async function () {
                const result = await apiCall(
                    "DELETE", `/api/radio/watch-terms/${encodeURIComponent(term.id)}`,
                    undefined, "The keyword could not be deleted");
                if (result.ended) return false;
                if (result.error) {
                    showError(rowError(row), result.error);
                    return false;
                }
                row.remove();
                updateSummaryText();
                return true;
            });

            const controls = element("div", "radio-admin-controls");
            controls.appendChild(toggleLabel);
            controls.appendChild(del);
            row.appendChild(main);
            row.appendChild(controls);
            return row;
        }

        async function load() {
            if (tab.busy) return;
            tab.busy = true;
            loading.hidden = false;
            const result = await apiCall("GET", "/api/radio/watch-terms", undefined,
                "Alert keywords could not be loaded");
            tab.busy = false;
            loading.hidden = true;
            if (result.ended) return;
            if (result.error) {
                showError(error, result.error);
                return;
            }
            hideError(error);
            list.textContent = "";
            firstArray(result.payload, ["terms", "watch_terms", "items"])
                .map(normalizeTerm)
                .filter(Boolean)
                .forEach(function (term) {
                    list.appendChild(renderTerm(term));
                });
            updateSummaryText();
            tab.loaded = true;
            tab.updatedAt = Date.now();
            if (tab.key === activeKey) showUpdated(tab);
        }
        tab.load = load;

        // "All channels" and the specific channels exclude each other.
        boxes.forEach(function (box) {
            box.addEventListener("change", function () {
                if (box === allBox && box.checked) {
                    boxes.forEach(function (other) {
                        if (other !== allBox) other.checked = false;
                    });
                } else if (box !== allBox && box.checked && allBox) {
                    allBox.checked = false;
                }
                const anySpecific = boxes.some(function (other) {
                    return other !== allBox && other.checked;
                });
                if (!anySpecific && allBox) allBox.checked = true;
            });
        });

        addForm.addEventListener("submit", async function (event) {
            event.preventDefault();
            hideError(formError);
            const text = phrase.value.trim();
            if (text.length < 2 || text.length > 80) {
                showError(formError, "Enter a phrase of 2 to 80 characters.");
                phrase.focus();
                return;
            }
            const channels = boxes
                .filter(function (box) {
                    return box !== allBox && box.checked;
                })
                .map(function (box) {
                    return box.value;
                });
            addButton.disabled = true;
            const result = await apiCall(
                "POST", "/api/radio/watch-terms",
                { phrase: text, channels: channels, enabled: true },
                "The keyword could not be added");
            addButton.disabled = false;
            if (result.ended) return;
            if (result.error) {
                showError(formError, result.error);
                return;
            }
            phrase.value = "";
            await load();
            phrase.focus();
        });
    }

    // ---- Transcript fixes

    function normalizeFix(raw) {
        if (!raw || typeof raw !== "object") return null;
        const id = raw.replacement_id !== undefined ? raw.replacement_id : raw.id;
        const find = raw.find !== undefined ? raw.find : raw.find_text;
        const replace = raw.replace !== undefined ? raw.replace : raw.replace_text;
        if (id === undefined || id === null || typeof find !== "string") return null;
        return {
            id: id,
            find: find,
            replace: typeof replace === "string" ? replace : "",
            wholeWord: raw.whole_word !== false,
        };
    }

    function setupFixesTab(tab) {
        const list = byId("radio-fix-list");
        const empty = byId("radio-fix-empty");
        const loading = byId("radio-fix-loading");
        const error = byId("radio-fix-error");
        const addForm = byId("radio-fix-form");
        const findInput = byId("radio-fix-find");
        const replaceInput = byId("radio-fix-replace");
        const wholeBox = byId("radio-fix-whole");
        const formError = byId("radio-fix-form-error");
        const total = byId("radio-fix-total");
        const addButton = byId("radio-fix-add");
        if (!list || !empty || !loading || !error || !addForm || !findInput || !replaceInput ||
            !wholeBox || !formError || !addButton) {
            return;
        }

        function updateSummaryText() {
            const n = list.children.length;
            empty.hidden = n > 0;
            if (total) total.textContent = n ? `${n} fix${n === 1 ? "" : "es"}` : "";
        }

        function renderFix(fix) {
            const row = element("li", "radio-admin-row");
            row.dataset.replacementId = String(fix.id);
            const main = element("div", "radio-admin-main");
            main.appendChild(element("code", "radio-fix-find", fix.find));
            main.appendChild(icon("bi-arrow-right"));
            const replacement = element("code", "radio-fix-replace", fix.replace || "(blank)");
            if (!fix.replace) replacement.classList.add("is-empty");
            main.appendChild(replacement);
            main.appendChild(element("span",
                `radio-badge ${fix.wholeWord ? "is-info" : ""}`,
                fix.wholeWord ? "Whole word" : "Any match"));
            const del = makeDeleteControl(`fix ${fix.find}`, async function () {
                const result = await apiCall(
                    "DELETE", `/api/radio/replacements/${encodeURIComponent(fix.id)}`,
                    undefined, "The fix could not be deleted");
                if (result.ended) return false;
                if (result.error) {
                    showError(rowError(row), result.error);
                    return false;
                }
                row.remove();
                updateSummaryText();
                return true;
            });
            const controls = element("div", "radio-admin-controls");
            controls.appendChild(del);
            row.appendChild(main);
            row.appendChild(controls);
            return row;
        }

        async function load() {
            if (tab.busy) return;
            tab.busy = true;
            loading.hidden = false;
            const result = await apiCall("GET", "/api/radio/replacements", undefined,
                "Transcript fixes could not be loaded");
            tab.busy = false;
            loading.hidden = true;
            if (result.ended) return;
            if (result.error) {
                showError(error, result.error);
                return;
            }
            hideError(error);
            list.textContent = "";
            firstArray(result.payload, ["replacements", "fixes", "rules", "items"])
                .map(normalizeFix)
                .filter(Boolean)
                .forEach(function (fix) {
                    list.appendChild(renderFix(fix));
                });
            updateSummaryText();
            tab.loaded = true;
            tab.updatedAt = Date.now();
            if (tab.key === activeKey) showUpdated(tab);
        }
        tab.load = load;

        addForm.addEventListener("submit", async function (event) {
            event.preventDefault();
            hideError(formError);
            const find = findInput.value.trim();
            const replace = replaceInput.value;
            if (find.length < 1 || find.length > 80) {
                showError(formError, "Enter text to find, 1 to 80 characters.");
                findInput.focus();
                return;
            }
            if (find.toLowerCase() === replace.trim().toLowerCase()) {
                showError(formError, "The replacement must differ from the text to find.");
                replaceInput.focus();
                return;
            }
            addButton.disabled = true;
            const result = await apiCall(
                "POST", "/api/radio/replacements",
                { find: find, replace: replace.trim(), whole_word: wholeBox.checked },
                "The fix could not be added");
            addButton.disabled = false;
            if (result.ended) return;
            if (result.error) {
                showError(formError, result.error);
                return;
            }
            findInput.value = "";
            replaceInput.value = "";
            await load();
            findInput.focus();
        });
    }

    // ---- Hint words

    function normalizeHint(raw) {
        if (!raw || typeof raw !== "object") return null;
        const id = raw.hint_id !== undefined ? raw.hint_id : raw.id;
        if (id === undefined || id === null || typeof raw.phrase !== "string") return null;
        return {
            id: id,
            phrase: raw.phrase,
            createdBy: typeof raw.created_by === "string" ? raw.created_by : "",
            createdAt: typeof raw.created_at === "string" ? raw.created_at : "",
        };
    }

    function setupHintsTab(tab) {
        const list = byId("radio-hint-list");
        const empty = byId("radio-hint-empty");
        const loading = byId("radio-hint-loading");
        const error = byId("radio-hint-error");
        const addForm = byId("radio-hint-form");
        const phraseInput = byId("radio-hint-phrase");
        const formError = byId("radio-hint-form-error");
        const total = byId("radio-hint-total");
        const addButton = byId("radio-hint-add");
        if (!list || !empty || !loading || !error || !addForm || !phraseInput || !formError ||
            !addButton) {
            return;
        }

        function updateSummaryText() {
            const n = list.children.length;
            empty.hidden = n > 0;
            if (total) total.textContent = n ? `${n} hint word${n === 1 ? "" : "s"}` : "";
        }

        function renderHint(hint) {
            const row = element("li", "radio-admin-row");
            row.dataset.hintId = String(hint.id);
            const main = element("div", "radio-admin-main");
            main.appendChild(element("strong", "radio-admin-phrase", hint.phrase));
            const when = hint.createdAt ? formatTime(hint.createdAt) : "";
            const who = hint.createdBy ? `Added by ${hint.createdBy}` : "Added";
            main.appendChild(element("span", "radio-admin-meta", when ? `${who}, ${when}` : who));
            const del = makeDeleteControl(`hint word ${hint.phrase}`, async function () {
                const result = await apiCall(
                    "DELETE", `/api/radio/hints/${encodeURIComponent(hint.id)}`,
                    undefined, "The hint word could not be deleted");
                if (result.ended) return false;
                if (result.error) {
                    showError(rowError(row), result.error);
                    return false;
                }
                row.remove();
                updateSummaryText();
                return true;
            });
            const controls = element("div", "radio-admin-controls");
            controls.appendChild(del);
            row.appendChild(main);
            row.appendChild(controls);
            return row;
        }

        async function load() {
            if (tab.busy) return;
            tab.busy = true;
            loading.hidden = false;
            const result = await apiCall("GET", "/api/radio/hints", undefined,
                "Hint words could not be loaded");
            tab.busy = false;
            loading.hidden = true;
            if (result.ended) return;
            if (result.error) {
                showError(error, result.error);
                return;
            }
            hideError(error);
            list.textContent = "";
            firstArray(result.payload, ["hints", "items"])
                .map(normalizeHint)
                .filter(Boolean)
                .forEach(function (hint) {
                    list.appendChild(renderHint(hint));
                });
            updateSummaryText();
            tab.loaded = true;
            tab.updatedAt = Date.now();
            if (tab.key === activeKey) showUpdated(tab);
        }
        tab.load = load;

        addForm.addEventListener("submit", async function (event) {
            event.preventDefault();
            hideError(formError);
            const text = phraseInput.value.trim();
            if (text.length < MIN_HINT || text.length > MAX_HINT) {
                showError(formError,
                    `Enter a name or phrase of ${MIN_HINT} to ${MAX_HINT} characters.`);
                phraseInput.focus();
                return;
            }
            if (CONTROL_CHARS.test(text) || /[\r\n]/.test(text)) {
                showError(formError, "Use plain text on one line.");
                phraseInput.focus();
                return;
            }
            addButton.disabled = true;
            const result = await apiCall(
                "POST", "/api/radio/hints", { phrase: text },
                "The hint word could not be added");
            addButton.disabled = false;
            if (result.ended) return;
            if (result.error) {
                // A duplicate arrives as HTTP 409 with the server's reason.
                showError(formError, result.error);
                phraseInput.focus();
                return;
            }
            phraseInput.value = "";
            await load();
            phraseInput.focus();
        });
    }

    // ---- Units (radio ID -> unit name)

    function normalizeUnit(raw) {
        if (!raw || typeof raw !== "object") return null;
        if (raw.radio_id === undefined || raw.radio_id === null) return null;
        const id = String(raw.radio_id);
        if (!RADIO_ID_PATTERN.test(id)) return null;
        return {
            id: id,
            name: typeof raw.name === "string" ? raw.name.trim().slice(0, MAX_UNIT_NAME) : "",
            segments: wholeNumber(raw.segments_30d) || 0,
            lastSeen: typeof raw.last_seen_at === "string" ? raw.last_seen_at : "",
        };
    }

    // Names are plain text on one line, 1 to 60 characters. Returns a problem or "".
    function unitNameProblem(name) {
        if (name.length < 1 || name.length > MAX_UNIT_NAME) {
            return `Enter a unit name of 1 to ${MAX_UNIT_NAME} characters.`;
        }
        if (CONTROL_CHARS.test(name) || /[\r\n\t]/.test(name)) {
            return "Use plain text on one line.";
        }
        return "";
    }

    function setupUnitsTab(tab) {
        const list = byId("radio-unit-list");
        const empty = byId("radio-unit-empty");
        const loading = byId("radio-unit-loading");
        const error = byId("radio-unit-error");
        const addForm = byId("radio-unit-form");
        const idInput = byId("radio-unit-new-id");
        const nameInput = byId("radio-unit-new-name");
        const formError = byId("radio-unit-form-error");
        const total = byId("radio-unit-total");
        const status = byId("radio-unit-status");
        const addButton = byId("radio-unit-add");
        const onlyUnnamed = byId("radio-unit-unnamed");
        if (!list || !empty || !loading || !error || !addForm || !idInput || !nameInput ||
            !formError || !addButton || !onlyUnnamed) {
            return;
        }
        let maxUnits = 2000;

        function say(text) {
            if (status) status.textContent = text;
        }

        function updateSummaryText() {
            const rows = list.children.length;
            const named = list.querySelectorAll(".radio-unit-row.is-named").length;
            list.classList.toggle("is-only-unnamed", onlyUnnamed.checked);
            const visible = onlyUnnamed.checked ? rows - named : rows;
            empty.hidden = visible > 0;
            empty.textContent = rows === 0
                ? "No radio IDs seen yet. Add one below."
                : "Every radio ID listed has a name.";
            if (total) {
                total.textContent = rows
                    ? `${rows} radio ID${rows === 1 ? "" : "s"}, ${named} named ` +
                      `(up to ${maxUnits.toLocaleString("en-US")} names)`
                    : "";
            }
        }

        function focusRow(id) {
            const input = list.querySelector(`[data-radio-id="${id}"] .radio-unit-name`);
            if (input) input.focus({ preventScroll: true });
        }

        function replaceRow(row, unit) {
            if (!unit.name && unit.segments === 0) {
                // Named only and never heard: nothing is left to show.
                row.remove();
                updateSummaryText();
                idInput.focus({ preventScroll: true });
                return;
            }
            row.replaceWith(renderUnit(unit));
            updateSummaryText();
            focusRow(unit.id);
        }

        function renderUnit(unit) {
            const row = element("li",
                `radio-admin-row radio-unit-row${unit.name ? " is-named" : ""}`);
            row.dataset.radioId = unit.id;

            const main = element("div", "radio-admin-main");
            main.appendChild(element("strong", "radio-unit-id", unit.id));
            main.appendChild(element("span", "radio-admin-meta",
                `${unit.segments.toLocaleString("en-US")} segment${unit.segments === 1 ? "" : "s"} in 30 days`));
            const heard = unit.lastSeen ? formatTime(unit.lastSeen) : "";
            main.appendChild(element("span", "radio-admin-meta",
                heard ? `Last heard ${heard}` : "Not heard in 30 days"));

            const edit = element("form", "radio-unit-edit");
            edit.noValidate = true;
            const label = element("label", "radio-unit-name-label");
            label.appendChild(element("span", "radio-sr-only", `Unit name for radio ID ${unit.id}`));
            const input = element("input", "radio-unit-name");
            input.type = "text";
            input.maxLength = MAX_UNIT_NAME;
            input.value = unit.name;
            input.placeholder = "Unit name";
            input.autocomplete = "off";
            label.appendChild(input);
            const save = iconButton("radio-tool radio-unit-save", "bi-check-lg", "Save");
            save.type = "submit";
            save.setAttribute("aria-label", `Save name for radio ID ${unit.id}`);
            edit.appendChild(label);
            edit.appendChild(save);

            edit.addEventListener("submit", async function (event) {
                event.preventDefault();
                hideError(rowError(row));
                const name = input.value.trim();
                const problem = unitNameProblem(name);
                if (problem) {
                    showError(rowError(row), problem);
                    input.focus({ preventScroll: true });
                    return;
                }
                if (name === unit.name) {
                    say(`No change to radio ID ${unit.id}.`);
                    return;
                }
                save.disabled = true;
                input.readOnly = true;
                const result = await apiCall(
                    "PUT", `/api/radio/units/${encodeURIComponent(unit.id)}`,
                    { name: name }, "The unit name could not be saved");
                save.disabled = false;
                input.readOnly = false;
                if (result.ended) return;
                if (result.error) {
                    showError(rowError(row), result.error);
                    input.focus({ preventScroll: true });
                    return;
                }
                const reply = result.payload && typeof result.payload === "object"
                    ? normalizeUnit(result.payload.unit || result.payload) : null;
                unitsDirty = true;
                replaceRow(row, {
                    id: unit.id,
                    name: reply && reply.name ? reply.name : name,
                    segments: reply ? reply.segments : unit.segments,
                    lastSeen: reply ? reply.lastSeen : unit.lastSeen,
                });
                say(`Saved the name for radio ID ${unit.id}.`);
            });

            const controls = element("div", "radio-admin-controls");
            controls.appendChild(edit);
            if (unit.name) {
                controls.appendChild(makeDeleteControl(`name of radio ID ${unit.id}`, async function () {
                    const result = await apiCall(
                        "DELETE", `/api/radio/units/${encodeURIComponent(unit.id)}`,
                        undefined, "The unit name could not be cleared");
                    if (result.ended) return false;
                    if (result.error) {
                        showError(rowError(row), result.error);
                        return false;
                    }
                    unitsDirty = true;
                    replaceRow(row, {
                        id: unit.id, name: "", segments: unit.segments, lastSeen: unit.lastSeen,
                    });
                    say(`Cleared the name of radio ID ${unit.id}. Its segments keep the ID.`);
                    return true;
                }, { idle: "Clear", ask: "Clear this name?", yes: "Yes, clear", icon: "bi-eraser" }));
            }
            row.appendChild(main);
            row.appendChild(controls);
            return row;
        }

        async function load() {
            if (tab.busy) return;
            tab.busy = true;
            loading.hidden = false;
            const result = await apiCall("GET", "/api/radio/units", undefined,
                "Units could not be loaded");
            tab.busy = false;
            loading.hidden = true;
            if (result.ended) return;
            if (result.error) {
                showError(error, result.error);
                return;
            }
            hideError(error);
            const max = result.payload ? wholeNumber(result.payload.max_units) : null;
            if (max) maxUnits = max;
            list.textContent = "";
            firstArray(result.payload, ["units", "items"])
                .map(normalizeUnit)
                .filter(Boolean)
                .forEach(function (unit) {
                    list.appendChild(renderUnit(unit));
                });
            updateSummaryText();
            tab.loaded = true;
            tab.updatedAt = Date.now();
            if (tab.key === activeKey) showUpdated(tab);
        }
        tab.load = load;

        onlyUnnamed.addEventListener("change", updateSummaryText);

        addForm.addEventListener("submit", async function (event) {
            event.preventDefault();
            hideError(formError);
            const id = idInput.value.trim();
            const name = nameInput.value.trim();
            if (!RADIO_ID_PATTERN.test(id)) {
                showError(formError, "Enter a radio ID of 1 to 10 digits.");
                idInput.focus();
                return;
            }
            const problem = unitNameProblem(name);
            if (problem) {
                showError(formError, problem);
                nameInput.focus();
                return;
            }
            addButton.disabled = true;
            const result = await apiCall(
                "PUT", `/api/radio/units/${encodeURIComponent(id)}`, { name: name },
                "The unit name could not be saved");
            addButton.disabled = false;
            if (result.ended) return;
            if (result.error) {
                // 400 (bad value), 409 (table full) and 503 (not recorded) arrive
                // with the server's reason through the shared inline error path.
                showError(formError, result.error);
                return;
            }
            idInput.value = "";
            nameInput.value = "";
            unitsDirty = true;
            say(`Saved the name for radio ID ${id}.`);
            await load();
            idInput.focus();
        });
    }

    // ------------------------------------------------------------------- init

    Array.from(tablist.querySelectorAll('[role="tab"]')).forEach(registerTab);
    Array.from(tablist.querySelectorAll("[data-admin-tab]")).forEach(function (button) {
        const tab = registerAdminTab(button);
        if (!tab) return;
        if (tab.name === "watch") setupWatchTab(tab);
        else if (tab.name === "fixes") setupFixesTab(tab);
        else if (tab.name === "hints") setupHintsTab(tab);
        else if (tab.name === "units") setupUnitsTab(tab);
    });
    activateTab("", false);
    loadStats();
    statsTimer = window.setInterval(pollStats, STATS_REFRESH_MS);
    feedTimer = window.setInterval(autoRefresh, FEED_REFRESH_MS);
    if (window.LCDashSession) {
        window.LCDashSession.onEnd(stopPolling);
    }
})();
