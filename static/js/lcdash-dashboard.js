(function () {
    "use strict";

    const REFRESH_SECONDS = 30;
    const STALE_AFTER_SECONDS = 90;
    const REQUEST_TIMEOUT_MS = 15000;
    const SNAPSHOT_URL = "/api/operations/snapshot";

    let secondsUntilRefresh = REFRESH_SECONDS;
    let refreshInProgress = false;
    let lastSuccessfulRefresh = Date.now();
    let realtimeRefreshTimer = null;
    let realtimeSource = null;

    function element(id) {
        return document.getElementById(id);
    }

    function createElement(tagName, className, text) {
        const node = document.createElement(tagName);

        if (className) {
            node.className = className;
        }

        if (text !== undefined && text !== null) {
            node.textContent = String(text);
        }

        return node;
    }

    function createIcon(iconClass, extraClass) {
        const icon = createElement("i", "bi " + iconClass + (extraClass ? " " + extraClass : ""));
        icon.setAttribute("aria-hidden", "true");
        return icon;
    }

    function safeText(value, fallback) {
        const text = String(value ?? "").trim();
        return text || fallback || "";
    }

    function safeCount(value) {
        const parsed = Number.parseInt(value, 10);
        return Number.isFinite(parsed) && parsed >= 0 ? parsed : 0;
    }

    function safePriority(value) {
        const parsed = Number.parseInt(value, 10);
        return Number.isFinite(parsed) && parsed > 0 ? parsed : 999;
    }

    function callKey(call, index) {
        return safeText(call?.cfs_number, "unknown-call-" + index);
    }

    function callFingerprint(call) {
        return JSON.stringify({
            cfs_number: safeText(call?.cfs_number),
            incident_code: safeText(call?.incident_code),
            incident_description: safeText(call?.incident_description),
            location: safeText(call?.location),
            priority: safeText(call?.priority),
            agency: safeText(call?.agency),
            units: safeText(call?.units),
            status: safeText(call?.status),
            call_taker: safeText(call?.call_taker),
            call_datetime: safeText(call?.call_datetime)
        });
    }

    function replaceStatusHeading(text, stateClass, iconClass) {
        const heading = element("operations-status-heading");

        if (!heading) {
            return;
        }

        heading.className = stateClass;
        heading.replaceChildren(createIcon(iconClass, "me-2"), document.createTextNode(text));
    }

    function updateCadStatus(text, stateClass, iconClass) {
        const status = element("cad-status-value");

        if (!status) {
            return;
        }

        status.className = "ops-value " + stateClass;
        status.replaceChildren(createIcon(iconClass, "me-1"), document.createTextNode(text));
    }

    function setFreshness(text, stateClass) {
        const freshness = element("dashboard-freshness");

        if (!freshness) {
            return;
        }

        freshness.className = "view-mode-badge " + stateClass;
        freshness.textContent = text;
    }

    function markConnected(highPriorityCalls) {
        const statusBar = element("dashboard-status-bar");
        statusBar?.classList.remove("is-stale", "is-disconnected");

        updateCadStatus("CONNECTED", "ops-good", "bi-circle-fill");
        setFreshness("Live Supervisor View", "is-live");

        if (highPriorityCalls > 0) {
            replaceStatusHeading(
                "Attention Required",
                "ops-warning",
                "bi-exclamation-triangle-fill"
            );
        } else {
            replaceStatusHeading(
                "Operations Normal",
                "ops-good",
                "bi-check-circle-fill"
            );
        }
    }

    function markDisconnected() {
        const statusBar = element("dashboard-status-bar");
        statusBar?.classList.add("is-disconnected");

        updateCadStatus("RECONNECTING", "ops-danger", "bi-arrow-repeat");
        replaceStatusHeading(
            "CAD Connection Issue",
            "ops-danger",
            "bi-cloud-slash-fill"
        );
        setFreshness("Reconnecting - last known data", "is-reconnecting");
    }

    function markStaleIfNeeded() {
        const ageSeconds = Math.floor((Date.now() - lastSuccessfulRefresh) / 1000);

        if (ageSeconds < STALE_AFTER_SECONDS) {
            return;
        }

        element("dashboard-status-bar")?.classList.add("is-stale");
        updateCadStatus("DATA STALE", "ops-danger", "bi-exclamation-octagon-fill");
        replaceStatusHeading(
            "Operational Data Stale",
            "ops-danger",
            "bi-exclamation-octagon-fill"
        );
        setFreshness("Stale data - reconnecting", "is-stale");
    }

    function updateHighPriority(highPriorityCalls) {
        const highPriority = element("high-priority-status");

        if (!highPriority) {
            return;
        }

        if (highPriorityCalls > 0) {
            highPriority.className = "ops-value ops-warning";
            highPriority.textContent = highPriorityCalls + " ACTIVE";
        } else {
            highPriority.className = "ops-value ops-good";
            highPriority.textContent = "NONE";
        }
    }

    function updateOldestCall(oldestCallDateTime) {
        const value = element("oldest-call-value");
        const subtitle = element("oldest-call-subtitle");
        const card = element("oldest-call-kpi");

        if (!value || !subtitle || !card) {
            return;
        }

        if (oldestCallDateTime) {
            value.className = "kpi-number kpi-time call-elapsed";
            value.dataset.callTime = oldestCallDateTime;
            value.textContent = "--:--:--";
            subtitle.textContent = "Age of longest active incident";
            card.classList.add("alert-kpi");
        } else {
            value.className = "kpi-number kpi-time ops-good";
            delete value.dataset.callTime;
            value.textContent = "CLEAR";
            subtitle.textContent = "No active calls";
            card.classList.remove("alert-kpi");
        }
    }

    function updateAgencySummary(agencySummary) {
        const container = element("agency-summary-list");

        if (!container) {
            return;
        }

        const fragment = document.createDocumentFragment();
        const agencies = Array.isArray(agencySummary) ? agencySummary : [];

        if (!agencies.length) {
            fragment.append(createElement("span", "text-secondary", "No agency data returned."));
        } else {
            agencies.forEach(function (item) {
                const chip = createElement("span", "agency-chip");
                chip.append(
                    document.createTextNode(safeText(item.agency, "Unknown") + " "),
                    createElement("span", "agency-chip-count", safeCount(item.count))
                );
                fragment.append(chip);
            });
        }

        container.replaceChildren(fragment);
    }

    function createCallMetaRow(leftLabel, leftValue, rightLabel, rightValue, rightClass) {
        const row = createElement("div", "row mt-3 small");
        const left = createElement("div", "col-6");
        const right = createElement("div", "col-6 text-end");

        left.append(
            createElement("div", "label", leftLabel),
            createElement("div", "value" + (leftLabel === "STATUS" ? " status-text" : ""), leftValue)
        );
        right.append(
            createElement("div", "label", rightLabel),
            createElement("div", "value" + (rightClass ? " " + rightClass : ""), rightValue)
        );
        row.append(left, right);
        return row;
    }

    function createIncidentCard(call, index, animateUpdate) {
        const priority = safePriority(call.priority);
        const column = createElement("div", "col-xl-4 col-lg-6");
        const link = createElement("a", "incident-card-link");
        const cardPriority = [5, 10, 15, 20, 30].includes(priority) ? " priority-" + priority : "";
        const updateClass = animateUpdate ? " is-updated" : "";
        const card = createElement("div", "incident-card" + cardPriority + updateClass);
        const header = createElement("div", "d-flex justify-content-between align-items-start mb-2");
        const headerText = createElement("div");
        const code = createElement("div", "incident-code", safeText(call.incident_code, "UNKNOWN"));

        column.dataset.cfsNumber = callKey(call, index);
        column.dataset.callFingerprint = callFingerprint(call);
        link.href = "/calls/" + encodeURIComponent(safeText(call.cfs_number));
        link.target = "_blank";
        link.rel = "noopener";

        if (priority <= 15) {
            code.append(createElement("span", "badge text-bg-danger ms-2", "HIGH PRIORITY"));
        }

        headerText.append(
            code,
            createElement(
                "div",
                "incident-title",
                safeText(call.incident_description, "Incident")
            )
        );
        header.append(
            headerText,
            createElement(
                "div",
                "priority-badge",
                priority === 999 ? "PRI -" : "PRI " + priority
            )
        );

        const location = createElement("div", "incident-location mt-3");
        location.append(
            createIcon("bi-geo-alt-fill"),
            document.createTextNode(safeText(call.location, "Location not returned"))
        );

        const units = createElement("div", "incident-units mt-3");
        units.append(
            createIcon("bi-truck-front-fill"),
            document.createTextNode(safeText(call.units, "No units assigned"))
        );

        const statusRow = createCallMetaRow(
            "STATUS",
            safeText(call.status, "Open"),
            "ELAPSED",
            "--:--:--",
            "text-warning call-elapsed"
        );
        const elapsed = statusRow.querySelector(".call-elapsed");
        if (elapsed) {
            elapsed.dataset.callTime = safeText(call.call_datetime);
        }

        const detailRow = createCallMetaRow(
            "CALL TAKER",
            safeText(call.call_taker, "-"),
            "AGENCY",
            safeText(call.agency, "Unknown")
        );

        const footer = createElement(
            "div",
            "incident-footer mt-3 d-flex justify-content-between align-items-center"
        );
        const commandView = createElement("span", "text-info");
        commandView.append(
            document.createTextNode("Open Command View "),
            createIcon("bi-box-arrow-up-right")
        );
        footer.append(
            createElement("span", "", safeText(call.cfs_number, "Unknown CFS")),
            commandView
        );

        card.append(header, location, units, statusRow, detailRow, footer);
        link.append(card);
        column.append(link);
        return column;
    }

    function updateIncidentFeed(calls) {
        const container = element("incident-feed-content");
        const countBadge = element("incident-feed-count");
        const safeCalls = Array.isArray(calls) ? calls : [];

        if (countBadge) {
            countBadge.textContent = safeCalls.length + " ACTIVE";
        }

        if (!container) {
            return;
        }

        if (!safeCalls.length) {
            container.replaceChildren(
                createElement(
                    "div",
                    "text-center text-secondary py-5",
                    "No active calls returned from CAD."
                )
            );
            return;
        }

        let grid = container.querySelector("#incident-card-grid");

        if (!grid) {
            grid = createElement("div", "row g-4");
            grid.id = "incident-card-grid";
            container.replaceChildren(grid);
        }

        const existingByKey = new Map();
        Array.from(grid.children).forEach(function (column) {
            const key = safeText(column.dataset.cfsNumber);
            if (key) {
                existingByKey.set(key, column);
            }
        });

        const desiredColumns = safeCalls.map(function (rawCall, index) {
            const call = rawCall || {};
            const key = callKey(call, index);
            const fingerprint = callFingerprint(call);
            const existing = existingByKey.get(key);

            if (existing?.dataset.callFingerprint === fingerprint) {
                return existing;
            }

            const wasPreviouslyHydrated = Boolean(existing?.dataset.callFingerprint);
            const isNewCall = !existing;
            return createIncidentCard(
                call,
                index,
                wasPreviouslyHydrated || isNewCall
            );
        });

        let cursor = grid.firstElementChild;
        desiredColumns.forEach(function (column) {
            if (column === cursor) {
                cursor = cursor.nextElementSibling;
                return;
            }

            grid.insertBefore(column, cursor);
        });

        const desiredSet = new Set(desiredColumns);
        Array.from(grid.children).forEach(function (column) {
            if (!desiredSet.has(column)) {
                column.remove();
            }
        });
    }

    function applySnapshot(data) {
        const stats = data.dashboard_stats || {};
        const activeCalls = safeCount(stats.active_calls);
        const assignedUnits = safeCount(stats.assigned_units);
        const highPriorityCalls = safeCount(stats.high_priority_calls);
        const onSceneCalls = safeCount(stats.on_scene_calls);
        const lastUpdated = safeText(data.last_updated);

        element("active-calls-value").textContent = activeCalls;
        element("assigned-units-value").textContent = assignedUnits;
        element("on-scene-calls-value").textContent = onSceneCalls;

        updateHighPriority(highPriorityCalls);
        updateOldestCall(safeText(stats.oldest_call_datetime));
        updateAgencySummary(stats.agency_summary);
        updateIncidentFeed(data.calls);

        const lastUpdatedElement = element("last-updated");
        if (lastUpdatedElement) {
            lastUpdatedElement.dataset.lastUpdated = lastUpdated;
            LCDashTime.updateElementFromCadTime("last-updated", {timeOnly: true});
        }

        LCDashTime.updateCallElapsedTimers();
        markConnected(highPriorityCalls);
        lastSuccessfulRefresh = Date.now();
    }

    async function refreshDashboard() {
        if (refreshInProgress) {
            return;
        }

        refreshInProgress = true;
        const controller = new AbortController();
        const timeoutId = window.setTimeout(function () {
            controller.abort();
        }, REQUEST_TIMEOUT_MS);
        const countdown = element("refresh-countdown");
        if (countdown) {
            countdown.textContent = "Updating...";
        }

        try {
            const response = await fetch(SNAPSHOT_URL, {
                method: "GET",
                cache: "no-store",
                credentials: "same-origin",
                signal: controller.signal,
                headers: {"Accept": "application/json"}
            });

            if (!response.ok) {
                throw new Error("Snapshot request failed");
            }

            const data = await response.json();

            if (!data.connected) {
                markDisconnected();
                return;
            }

            applySnapshot(data);
        } catch (error) {
            markDisconnected();
        } finally {
            window.clearTimeout(timeoutId);
            refreshInProgress = false;
            secondsUntilRefresh = REFRESH_SECONDS;
            markStaleIfNeeded();
        }
    }

    function scheduleRealtimeRefresh() {
        if (realtimeRefreshTimer !== null) {
            window.clearTimeout(realtimeRefreshTimer);
        }

        realtimeRefreshTimer = window.setTimeout(function () {
            realtimeRefreshTimer = null;
            if (refreshInProgress) {
                secondsUntilRefresh = 0;
                return;
            }
            refreshDashboard();
        }, 500);
    }

    function startRealtimeEvents() {
        if (!("EventSource" in window)) {
            return;
        }

        realtimeSource = new EventSource("/api/operations/events");
        realtimeSource.addEventListener(
            "operations_changed",
            scheduleRealtimeRefresh
        );
    }

    function timerTick() {
        LCDashTime.updateElementFromCadTime("last-updated", {timeOnly: true});
        LCDashTime.updateCallElapsedTimers();
        LCDashTime.updateLocalTime("dashboard-local-time");
        markStaleIfNeeded();

        const countdown = element("refresh-countdown");

        if (!refreshInProgress && countdown) {
            countdown.textContent = Math.max(secondsUntilRefresh, 0) + "s";
        }

        if (secondsUntilRefresh <= 0) {
            refreshDashboard();
            return;
        }

        secondsUntilRefresh -= 1;
    }

    document.addEventListener("visibilitychange", function () {
        if (
            document.visibilityState === "visible" &&
            Date.now() - lastSuccessfulRefresh > REFRESH_SECONDS * 1000
        ) {
            secondsUntilRefresh = 0;
            timerTick();
        }
    });

    window.addEventListener("pagehide", function () {
        if (realtimeSource) {
            realtimeSource.close();
        }
    });

    startRealtimeEvents();
    timerTick();
    window.setInterval(timerTick, 1000);
})();
