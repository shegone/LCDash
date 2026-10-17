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
// Layout: one tab per channel plus "All channels", and (administrators only)
// "Alert keywords" and "Transcript fixes". Each channel tab is a feed of cards
// (an <ol>), not a table, so it reads the same on a phone. Each tab keeps its
// own keyset cursor and is fetched the first time it is opened.
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
// Auto-refresh: while the page is visible and nothing is playing, the active
// feed tab fetches its newest page every 30 s and prepends segments not yet
// shown. It never clears the list, moves the scroll position, touches
// playback, or overwrites a card that is being edited.
(function () {
    "use strict";

    const STATS_REFRESH_MS = 15000;
    const MAX_HINT = 60;
    const MIN_HINT = 2;
    const FEED_REFRESH_MS = 30000;
    const PAGE_SIZE = 50;
    const MAX_CORRECTION = 20000;
    const CHANNEL_PATTERN = /^[a-z0-9_]{1,32}$/;
    const CONTROL_CHARS = /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/;
    const AUDIO_ERROR_TEXT =
        "Audio could not be played. It may have expired, or your session ended.";
    const KEEP_NOTE = "Corrected audio and text are kept for 1 year.";
    const NO_SPEECH = "(no speech recognized)";

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
                segment.retained_until || ""].join("|");
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
        state.descriptor = `${channel} ${direction} segment from ${time}`;
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
        meta.appendChild(element("span", `radio-chip ${channelClass(channel)}`, channel));
        const dir = element("span", "radio-dir", direction);
        dir.title = direction === "TX" ? "Transmitted" : "Received";
        meta.appendChild(dir);
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
        const tab = {
            key: channel,
            kind: "feed",
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
        // New channels sit with the channels, before any administrator tab.
        tablist.insertBefore(button, tablist.querySelector("[data-admin-tab]"));
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
        activeKey = key;
        tabs.forEach(function (other) {
            const selected = other.key === key;
            other.button.setAttribute("aria-selected", selected ? "true" : "false");
            other.button.tabIndex = selected ? 0 : -1;
            other.panel.hidden = !selected;
        });
        // The filters only apply to the transcript feeds.
        form.hidden = tab.kind !== "feed";
        if (focus) tab.button.focus();
        showUpdated(tab);
        if (tab.kind === "admin") {
            tab.load();
            return;
        }
        if (!tab.loaded) {
            loadFirst(tab);
        } else if (Date.now() - tab.updatedAt > FEED_REFRESH_MS) {
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

    // Undecoded payload types across all listener rows:
    // [{type, packets, where: [names]}], sorted by payload type.
    function undecodedAudio(rows) {
        const byType = new Map();
        rows.forEach(function (row) {
            const other = row.other_payload_types;
            if (!other || typeof other !== "object") return;
            Object.keys(other).forEach(function (type) {
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
            tiles.appendChild(statTile(String(count), `${name}, last 24 h`, false));
            ensureChannelTab(name);
            counts.set(name, count);
            total += count;
        });
        const last = stats.last_received_at;
        tiles.appendChild(statTile(
            last ? `${formatTime(last)} (${formatAge(last)})` : "Nothing received yet",
            "Last segment received",
            true));
        statsBox.appendChild(tiles);

        if (undecoded.length) statsBox.appendChild(renderWarning(undecoded));
        if (state !== "never") statsBox.appendChild(renderDetail(listener, state));
        announceHealth(state, undecoded);

        tabs.forEach(function (tab) {
            if (tab.kind !== "feed") return;
            if (tab.key === "") {
                setTabCount(tab, Number.isFinite(Number(stats.total)) ? Number(stats.total) : total);
            } else {
                setTabCount(tab, counts.has(tab.key) ? counts.get(tab.key) : 0);
            }
        });
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
    }

    function autoRefresh() {
        if (document.visibilityState !== "visible") return;
        if (audioActive()) return;
        const tab = tabs.get(activeKey);
        if (tab && tab.kind === "feed") refreshNewest(tab);
    }

    function applyFilters() {
        const active = tabs.get(activeKey);
        if (hasEditingCard()) {
            // Reloading would throw away an unsaved correction.
            if (active && active.kind === "feed") {
                showError(active.error, "Save or cancel the open edit before changing the filter.");
            }
            return;
        }
        tabs.forEach(function (tab) {
            if (tab.kind === "feed" && tab.key !== activeKey) clearTab(tab);
        });
        if (active && active.kind === "feed") loadFirst(active);
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
    function makeDeleteControl(label, run) {
        const wrap = element("span", "radio-delete");
        wrap.dataset.state = "idle";
        function idle(focusIt) {
            wrap.textContent = "";
            wrap.dataset.state = "idle";
            const button = iconButton("radio-tool is-danger", "bi-trash3", "Delete");
            button.setAttribute("aria-label", `Delete ${label}`);
            button.addEventListener("click", askConfirm);
            wrap.appendChild(button);
            if (focusIt) button.focus({ preventScroll: true });
        }
        function askConfirm() {
            wrap.textContent = "";
            wrap.dataset.state = "confirm";
            wrap.setAttribute("role", "group");
            wrap.appendChild(element("span", "radio-confirm-text", "Delete this?"));
            const yes = iconButton("radio-tool is-danger", "bi-trash3", "Yes, delete");
            yes.setAttribute("aria-label", `Yes, delete ${label}`);
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
            wrap.appendChild(element("span", `radio-chip ${channelClass(name)}`, name));
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

    // ------------------------------------------------------------------- init

    Array.from(tablist.querySelectorAll('[role="tab"]')).forEach(registerTab);
    Array.from(tablist.querySelectorAll("[data-admin-tab]")).forEach(function (button) {
        const tab = registerAdminTab(button);
        if (!tab) return;
        if (tab.name === "watch") setupWatchTab(tab);
        else if (tab.name === "fixes") setupFixesTab(tab);
        else if (tab.name === "hints") setupHintsTab(tab);
    });
    activateTab("", false);
    loadStats();
    statsTimer = window.setInterval(pollStats, STATS_REFRESH_MS);
    feedTimer = window.setInterval(autoRefresh, FEED_REFRESH_MS);
    if (window.LCDashSession) {
        window.LCDashSession.onEnd(stopPolling);
    }
})();
