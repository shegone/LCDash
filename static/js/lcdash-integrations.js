(function () {
    "use strict";

    const HEALTH_URL = "/api/integrations/centralsquare/health";
    const EVENTS_URL = "/api/operations/events";
    const REFRESH_MILLISECONDS = 30000;

    function element(id) {
        return document.getElementById(id);
    }

    function initialHealth() {
        const dataElement = element("integration-health-data");
        if (!dataElement) {
            return {};
        }

        try {
            return JSON.parse(dataElement.textContent || "{}");
        } catch (error) {
            return {};
        }
    }

    function formatTimestamp(value) {
        if (!value) {
            return "Not observed yet";
        }
        return LCDashTime.formatCadDisplayTime(value);
    }

    function setText(id, value) {
        const target = element(id);
        if (target) {
            target.textContent = value;
        }
    }

    function setOverall(health) {
        const overall = element("integration-overall");
        const label = element("integration-overall-label");
        if (overall) {
            overall.className = "integration-overall " + (health.status || "degraded");
        }
        if (label) {
            label.textContent = health.status_label || "Status unavailable";
        }
    }

    function setStatusValue(id, text, className) {
        const target = element(id);
        if (!target) {
            return;
        }
        target.textContent = text;
        target.className = className;
    }

    function renderSource(sourceName, source) {
        const card = element(sourceName + "-delivery-card");
        if (card) {
            card.className = "delivery-source " + (source.status || "unavailable");
        }

        setText(sourceName + "-status-label", source.status_label || "Unavailable");
        setText(sourceName + "-unique-events", source.unique_events || 0);
        setText(
            sourceName + "-duplicate-deliveries",
            source.duplicate_deliveries || 0
        );
        setText(sourceName + "-total-deliveries", source.total_deliveries || 0);
        setText(
            sourceName + "-latest-delivery",
            formatTimestamp(source.latest_delivery)
        );
    }

    function renderHealth(health) {
        setOverall(health);
        setStatusValue(
            "receiver-status",
            health.receiver_configured ? "READY" : "NOT CONFIGURED",
            health.receiver_configured ? "is-good" : "is-warning"
        );
        setStatusValue(
            "metadata-status",
            health.database_available ? "AVAILABLE" : "UNAVAILABLE",
            health.database_available ? "is-good" : "is-warning"
        );
        setText("health-generated-at", formatTimestamp(health.generated_at));

        const sources = health.sources || {};
        renderSource("cfs", sources.cfs || {});
        renderSource("units", sources.units || {});
    }

    async function refreshHealth() {
        try {
            const response = await fetch(HEALTH_URL, {
                headers: {"Accept": "application/json"},
                cache: "no-store"
            });
            // A poll that was bounced to the identity provider must stop, not
            // retry: concurrent polls racing a real login are what redeem an
            // authorization code twice (static/js/lcdash-session.js).
            if (window.LCDashSession && window.LCDashSession.check(response)) return;
            if (!response.ok) {
                throw new Error("Health request failed.");
            }
            renderHealth(await response.json());
        } catch (error) {
            setOverall({
                status: "degraded",
                status_label: "Health check unavailable"
            });
        }
    }

    function startEventStream() {
        if (!("EventSource" in window)) {
            setStatusValue(
                "integration-stream-status",
                "30S BACKUP",
                "is-warning"
            );
            setText(
                "integration-stream-detail",
                "Browser update channel is unavailable"
            );
            return;
        }

        const source = new EventSource(EVENTS_URL);
        // EventSource reconnects on its own every few seconds. After a
        // session ends that turns into a stream of unauthenticated requests
        // racing the user's real login (static/js/lcdash-session.js). Stop
        // the stream instead.
        if (window.LCDashSession) {
            window.LCDashSession.onEnd(function () {
                source.close();
            });
        }
        source.addEventListener("open", function () {
            setStatusValue(
                "integration-stream-status",
                "UPDATE CHANNEL",
                "is-good"
            );
            setText(
                "integration-stream-detail",
                "Application update channel open; CAD availability is shown separately"
            );
        });
        source.addEventListener("operations_changed", function () {
            setText(
                "integration-stream-detail",
                "Application update received " + new Date().toLocaleTimeString()
            );
            window.setTimeout(refreshHealth, 350);
        });
        source.addEventListener("error", function () {
            // A closed EventSource means the browser has given up; an ended
            // session is the common cause, and retrying into the login flow
            // is exactly what must not happen.
            if (source.readyState === 2 && window.LCDashSession) {
                window.LCDashSession.end();
                return;
            }
            setStatusValue(
                "integration-stream-status",
                "30S BACKUP",
                "is-warning"
            );
            setText(
                "integration-stream-detail",
                "Update channel reconnecting automatically"
            );
        });
    }

    renderHealth(initialHealth());
    startEventStream();
    const healthPollTimer = window.setInterval(refreshHealth, REFRESH_MILLISECONDS);
    if (window.LCDashSession) {
        window.LCDashSession.onEnd(function () {
            window.clearInterval(healthPollTimer);
        });
    }
})();
