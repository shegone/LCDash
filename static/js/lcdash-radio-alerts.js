// Dashboard panel: "Radio keyword alerts".
//
// Shows radio segments whose text matched an administrator's watch term. It is
// a visible panel and nothing more: no sound, no browser notification, nothing
// sent anywhere. Dismissing an alert only hides it from this panel for
// everyone; it is not an operational acknowledgement of anything.
//
// Polls GET /api/radio/alerts every 15 s using the LCDashSession pattern: it
// stops when the session ends, pauses while the tab is hidden, and hides the
// whole panel (silently) if the API answers 403/404 because this role has no
// access. Every server string is inserted with textContent; the highlighted
// phrase is built from DOM nodes (<mark>), never HTML.
(function () {
    "use strict";

    const POLL_MS = 15000;
    const MAX_ROWS = 25;
    const AUDIO_ERROR_TEXT =
        "Audio could not be played. It may have expired, or your session ended.";

    const panel = document.getElementById("radio-alerts-panel");
    const list = document.getElementById("radio-alerts-list");
    const empty = document.getElementById("radio-alerts-empty");
    const errorBox = document.getElementById("radio-alerts-error");
    const countBadge = document.getElementById("radio-alerts-count");
    const status = document.getElementById("radio-alerts-status");
    const more = document.getElementById("radio-alerts-more");
    if (!panel || !list || !empty || !errorBox || !countBadge || !status || !more) return;

    let timer = null;
    let inFlight = false;
    let stopped = false;
    let lastFetch = 0;
    let firstLoad = true;
    const rows = new Map(); // alert_id -> row node

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

    function formatTime(value) {
        if (!value) return "";
        if (window.LCDashTime && typeof window.LCDashTime.formatCadDisplayTime === "function") {
            return window.LCDashTime.formatCadDisplayTime(value);
        }
        return String(value);
    }

    function chipClass(channel) {
        // "law_ward_vhf" and the like share the colour of their group.
        for (const group of ["law", "ems", "fire"]) {
            if (channel === group || channel.startsWith(`${group}_`)) {
                return `radio-alert-chip is-${group}`;
            }
        }
        return "radio-alert-chip";
    }

    function showError(text) {
        errorBox.hidden = false;
        errorBox.textContent = text;
    }

    async function readDetail(response) {
        try {
            const body = await response.json();
            return typeof body.detail === "string" ? body.detail : "";
        } catch (error) {
            return "";
        }
    }

    // ------------------------------------------------------------------ audio

    // One Audio object for the whole panel; no source until Play is pressed.
    const player = new Audio();
    player.preload = "none";
    let playing = null; // {controls, state}

    function paint(controls, state) {
        const label = state === "playing" ? "Pause" : state === "loading" ? "Loading..." : "Play";
        const name = state === "playing" ? "bi-pause-fill"
            : state === "loading" ? "bi-hourglass-split" : "bi-play-fill";
        controls.icon.className = `bi ${name}`;
        controls.label.textContent = label;
        controls.button.classList.toggle("is-playing", state === "playing");
        controls.button.setAttribute("aria-label",
            `${state === "playing" ? "Pause" : "Play"} audio, ${controls.descriptor}`);
    }

    function release() {
        if (playing) {
            paint(playing.controls, "idle");
            playing = null;
        }
    }

    function failPlay(controls) {
        if (playing && playing.controls === controls) playing = null;
        paint(controls, "idle");
        controls.note.hidden = false;
        controls.note.textContent = AUDIO_ERROR_TEXT;
    }

    function pressPlay(alert, controls) {
        if (playing && playing.controls === controls) {
            if (playing.state === "playing") {
                player.pause();
                return;
            }
            if (playing.state === "paused") {
                playing.state = "loading";
                paint(controls, "loading");
                const resumed = player.play();
                if (resumed && resumed.catch) {
                    resumed.catch(function (error) {
                        if (!playing || playing.controls !== controls) return;
                        if (error && error.name === "AbortError") return;
                        failPlay(controls);
                    });
                }
                return;
            }
            if (playing.state === "loading") {
                player.pause();
                playing.state = "paused";
                paint(controls, "paused");
                return;
            }
        }
        if (playing) {
            player.pause();
            release();
        }
        controls.note.hidden = true;
        controls.note.textContent = "";
        playing = { controls: controls, state: "loading" };
        paint(controls, "loading");
        player.src = `/api/radio/segments/${encodeURIComponent(alert.segment_id)}/audio`;
        const started = player.play();
        if (started && started.catch) {
            started.catch(function (error) {
                if (!playing || playing.controls !== controls) return;
                if (error && error.name === "AbortError") return;
                failPlay(controls);
            });
        }
    }

    player.addEventListener("playing", function () {
        if (!playing) return;
        playing.state = "playing";
        paint(playing.controls, "playing");
    });
    player.addEventListener("pause", function () {
        if (!playing || playing.state !== "playing" || player.ended) return;
        playing.state = "paused";
        paint(playing.controls, "paused");
    });
    player.addEventListener("ended", release);
    player.addEventListener("error", function () {
        if (playing && player.error) failPlay(playing.controls);
    });

    // ------------------------------------------------------------------- rows

    // Snippet with every occurrence of the phrase wrapped in a <mark> node.
    function buildSnippet(snippet, phrase) {
        const paragraph = element("p", "radio-alert-snippet");
        const text = typeof snippet === "string" ? snippet : "";
        const needle = typeof phrase === "string" ? phrase.trim() : "";
        if (!needle) {
            paragraph.textContent = text;
            return paragraph;
        }
        const escaped = needle.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
        const pattern = new RegExp(escaped, "gi");
        let last = 0;
        let match = pattern.exec(text);
        while (match !== null) {
            if (match[0].length === 0) {
                pattern.lastIndex += 1;
            } else {
                if (match.index > last) {
                    paragraph.appendChild(document.createTextNode(text.slice(last, match.index)));
                }
                paragraph.appendChild(element("mark", "radio-alert-mark", match[0]));
                last = match.index + match[0].length;
            }
            match = pattern.exec(text);
        }
        if (last < text.length) {
            paragraph.appendChild(document.createTextNode(text.slice(last)));
        }
        return paragraph;
    }

    function removeRow(id) {
        const row = rows.get(id);
        if (!row) return;
        if (playing && row.contains(playing.controls.button)) {
            player.pause();
            release();
        }
        row.remove();
        rows.delete(id);
    }

    function buildRow(alert) {
        const id = String(alert.alert_id);
        const channel = String(alert.channel || "");
        const when = formatTime(alert.started_at || alert.created_at);
        const descriptor = `${channel} channel, ${when}, matched ${alert.phrase}`;
        const row = element("li", "radio-alert");
        row.dataset.alertId = id;

        const head = element("div", "radio-alert-head");
        head.appendChild(element("span", chipClass(channel), channel || "radio"));
        head.appendChild(element("span", "radio-alert-time", when));
        if (alert.text_corrected) {
            head.appendChild(element("span", "radio-alert-tag", "Corrected text"));
        }
        row.appendChild(head);
        row.appendChild(buildSnippet(alert.snippet, alert.phrase));

        const actions = element("div", "radio-alert-actions");
        if (alert.audio_available) {
            const play = element("button", "radio-alert-button is-primary");
            play.type = "button";
            const playIcon = icon("bi-play-fill");
            const playLabel = element("span", "", "Play");
            play.appendChild(playIcon);
            play.appendChild(playLabel);
            const note = element("span", "radio-alert-note");
            note.hidden = true;
            const controls = {
                button: play, icon: playIcon, label: playLabel, note: note, descriptor: descriptor,
            };
            paint(controls, "idle");
            play.addEventListener("click", function () {
                pressPlay(alert, controls);
            });
            actions.appendChild(play);
            row.appendChild(note);
        } else {
            actions.appendChild(element("span", "radio-alert-note", "Audio expired"));
        }

        const dismiss = element("button", "radio-alert-button");
        dismiss.type = "button";
        dismiss.appendChild(icon("bi-x-lg"));
        dismiss.appendChild(element("span", "", "Dismiss"));
        dismiss.setAttribute("aria-label", `Dismiss alert, ${descriptor}`);
        const failure = element("div", "radio-alert-failure");
        failure.setAttribute("role", "alert");
        failure.hidden = true;
        dismiss.addEventListener("click", async function () {
            dismiss.disabled = true;
            failure.hidden = true;
            try {
                const response = await fetch(
                    `/api/radio/alerts/${encodeURIComponent(alert.alert_id)}/dismiss`,
                    { method: "POST", headers: { "Accept": "application/json" } });
                if (window.LCDashSession && window.LCDashSession.check(response)) return;
                if (!response.ok) {
                    const detail = await readDetail(response);
                    failure.hidden = false;
                    failure.textContent =
                        `Dismiss failed (HTTP ${response.status}${detail ? `: ${detail}` : ""})`;
                    dismiss.disabled = false;
                    return;
                }
                removeRow(id);
                updateChrome();
            } catch (error) {
                failure.hidden = false;
                failure.textContent = "Dismiss failed (network error)";
                dismiss.disabled = false;
            }
        });
        actions.appendChild(dismiss);

        const link = element("a", "radio-alert-link");
        link.href = "/mindshare/radio";
        link.appendChild(document.createTextNode("Open radio page "));
        link.appendChild(icon("bi-box-arrow-up-right"));
        actions.appendChild(link);

        row.appendChild(actions);
        row.appendChild(failure);
        return row;
    }

    function updateChrome() {
        const shown = rows.size;
        empty.hidden = shown > 0;
        countBadge.textContent = String(shown);
        countBadge.classList.toggle("is-active", shown > 0);
        panel.classList.toggle("has-alerts", shown > 0);
    }

    // Make the list match `alerts` (newest first). Existing rows are kept so a
    // playing clip is never interrupted by a poll.
    function syncRows(alerts) {
        const wanted = alerts.slice(0, MAX_ROWS);
        const keep = new Set(wanted.map(function (a) {
            return String(a.alert_id);
        }));
        Array.from(rows.keys()).forEach(function (id) {
            if (!keep.has(id)) removeRow(id);
        });
        wanted.forEach(function (alert, index) {
            const id = String(alert.alert_id);
            let row = rows.get(id);
            if (!row) {
                row = buildRow(alert);
                rows.set(id, row);
            }
            if (list.children[index] !== row) {
                list.insertBefore(row, list.children[index] || null);
            }
        });
        const extra = alerts.length - wanted.length;
        more.hidden = extra <= 0;
        more.textContent = extra > 0
            ? `${extra} more not shown. Open the radio page to see the transcripts.` : "";
        updateChrome();
    }

    // ---------------------------------------------------------------- polling

    function stop() {
        stopped = true;
        if (timer !== null) {
            window.clearInterval(timer);
            timer = null;
        }
        if (playing) player.pause();
    }

    async function poll() {
        if (stopped || inFlight) return;
        if (document.visibilityState !== "visible") return;
        inFlight = true;
        lastFetch = Date.now();
        try {
            const response = await fetch("/api/radio/alerts", {
                headers: { "Accept": "application/json" },
                cache: "no-store",
            });
            if (window.LCDashSession && window.LCDashSession.check(response)) return;
            if (response.status === 403 || response.status === 404) {
                // This role has no access to radio alerts: hide the panel and
                // stop asking.
                panel.hidden = true;
                stop();
                return;
            }
            if (!response.ok) {
                const detail = await readDetail(response);
                panel.hidden = false;
                showError(`Radio alerts could not be loaded (HTTP ${response.status}${detail ? `: ${detail}` : ""})`);
                return;
            }
            const body = await response.json();
            const alerts = Array.isArray(body) ? body
                : (body && Array.isArray(body.alerts) ? body.alerts : []);
            const valid = alerts.filter(function (a) {
                return a && a.alert_id !== undefined && a.segment_id;
            });
            const before = rows.size;
            errorBox.hidden = true;
            errorBox.textContent = "";
            panel.hidden = false;
            syncRows(valid);
            if (!firstLoad && rows.size > before) {
                const added = rows.size - before;
                status.textContent = `${added} new radio keyword alert${added === 1 ? "" : "s"}`;
            }
            firstLoad = false;
        } catch (error) {
            panel.hidden = false;
            showError("Radio alerts could not be loaded (network error)");
        } finally {
            inFlight = false;
        }
    }

    document.addEventListener("visibilitychange", function () {
        if (document.visibilityState === "visible" && !stopped &&
            Date.now() - lastFetch > POLL_MS) {
            poll();
        }
    });

    if (window.LCDashSession) window.LCDashSession.onEnd(stop);
    poll();
    timer = window.setInterval(poll, POLL_MS);
})();
