(function () {
    "use strict";

    const refreshButton = document.getElementById("nga-refresh");
    const status = document.getElementById("nga-provider-status");
    const metricIds = {
        "nga-counties": "participating_counties",
        "nga-psaps": "psaps_online",
        "nga-sessions": "call_sessions_24h",
        "nga-availability": "network_availability_percent",
        "nga-location-confidence": "location_confidence_percent"
    };

    function updateTimestamp(value) {
        const element = document.getElementById("nga-generated-at");
        if (!element) {
            return;
        }
        element.dataset.cadTime = value || "";
        element.textContent = window.LCDashTime
            ? window.LCDashTime.formatCadDisplayTime(value)
            : value;
    }

    function updateMetric(id, value) {
        const element = document.getElementById(id);
        if (!element) {
            return;
        }
        if (id === "nga-sessions") {
            element.textContent = Number(value || 0).toLocaleString();
        } else if (id === "nga-availability" || id === "nga-location-confidence") {
            element.textContent = Number(value || 0) + "%";
        } else {
            element.textContent = value || 0;
        }
    }

    async function refreshOverview() {
        refreshButton.disabled = true;
        refreshButton.classList.add("nga-refreshing");
        try {
            const response = await fetch("/api/nga911/v1/intelligence/overview", {
                cache: "no-store",
                headers: { "Accept": "application/json" }
            });
            if (!response.ok) {
                throw new Error("NGA911 intelligence API returned " + response.status);
            }
            const overview = await response.json();
            if (!overview.synthetic_data) {
                throw new Error("The demonstration data marker was not present.");
            }
            Object.entries(metricIds).forEach(function (entry) {
                updateMetric(entry[0], (overview.summary || {})[entry[1]]);
            });
            status.textContent = overview.connection.status_label;
            document.getElementById("nga-api-latency").textContent =
                Number(overview.connection.api_latency_ms || 0) + " ms";
            updateTimestamp(overview.generated_at);
        } catch (error) {
            status.textContent = "SIMULATION REFRESH FAILED";
            status.title = error.message;
        } finally {
            refreshButton.disabled = false;
            refreshButton.classList.remove("nga-refreshing");
        }
    }

    refreshButton.addEventListener("click", refreshOverview);
    window.setInterval(refreshOverview, 60000);
}());
