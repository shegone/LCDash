window.LCDashTime = (function () {
    function parseCadDateTime(value) {
        if (!value) {
            return null;
        }

        let cleanedValue = String(value).trim();

        if (!cleanedValue) {
            return null;
        }

        if (cleanedValue.includes(".") && cleanedValue.endsWith("Z")) {
            cleanedValue = cleanedValue.replace(/\.(\d{3})\d*Z$/, ".$1Z");
        }

        const parsedDate = new Date(cleanedValue);

        if (isNaN(parsedDate.getTime())) {
            return null;
        }

        return parsedDate;
    }

    function formatElapsedTime(startDate) {
        const now = new Date();

        let totalSeconds = Math.floor((now - startDate) / 1000);

        if (totalSeconds < 0) {
            totalSeconds = 0;
        }

        const hours = Math.floor(totalSeconds / 3600);
        const minutes = Math.floor((totalSeconds % 3600) / 60);
        const seconds = totalSeconds % 60;

        return (
            String(hours).padStart(2, "0") + ":" +
            String(minutes).padStart(2, "0") + ":" +
            String(seconds).padStart(2, "0")
        );
    }

    function formatCadDisplayTime(value, options) {
        const settings = options || {};
        const date = parseCadDateTime(value);

        if (!date) {
            return value || "-";
        }

        const timePart = date.toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
            hour12: false
        });

        if (settings.timeOnly) {
            return timePart;
        }

        const datePart = date.toLocaleDateString([], {
            year: "numeric",
            month: "2-digit",
            day: "2-digit"
        });

        return datePart + " " + timePart;
    }

    function updateCadDisplayTimes(selector) {
        const timeElements = document.querySelectorAll(selector || ".cad-time");

        timeElements.forEach(function (element) {
            const rawValue = element.dataset.cadTime;

            if (!rawValue) {
                element.textContent = "-";
                return;
            }

            element.textContent = formatCadDisplayTime(rawValue);
        });
    }

    function updateElementFromCadTime(elementId, options) {
        const element = document.getElementById(elementId);

        if (!element) {
            return;
        }

        const rawValue =
            element.dataset.lastUpdated ||
            element.dataset.cadTime ||
            element.dataset.callTime ||
            "";

        if (!rawValue) {
            element.textContent = "Unknown";
            return;
        }

        element.textContent = formatCadDisplayTime(rawValue, options || {});
    }

    function updateElapsedElements(selector, datasetName, noTimeText) {
        const elements = document.querySelectorAll(selector);

        elements.forEach(function (element) {
            const rawValue = element.dataset[datasetName];
            const startDate = parseCadDateTime(rawValue);

            if (!startDate) {
                element.textContent = noTimeText || "--:--:--";
                return;
            }

            element.textContent = formatElapsedTime(startDate);
        });
    }

    function updateIncidentElapsed() {
        const elapsedElement = document.getElementById("incident-elapsed");

        if (!elapsedElement) {
            return;
        }

        const callStart = parseCadDateTime(elapsedElement.dataset.callTime);

        if (!callStart) {
            elapsedElement.textContent = "--:--:--";
            return;
        }

        elapsedElement.textContent = formatElapsedTime(callStart);
    }

    function updateCallElapsedTimers() {
        updateElapsedElements(".call-elapsed", "callTime", "--:--:--");
    }

    function updateUnitStatusTimers() {
        updateElapsedElements(".unit-status-timer", "statusStart", "No Time");
    }

    function updateLocalTime(elementId) {
        const localTimeElement = document.getElementById(elementId || "dashboard-local-time");

        if (!localTimeElement) {
            return;
        }

        localTimeElement.textContent = new Date().toLocaleTimeString();
    }

    function startClock(elementId) {
        const clockElement = document.getElementById(elementId || "clock");

        if (!clockElement) {
            return null;
        }

        function tick() {
            clockElement.innerHTML = new Date().toLocaleString();
        }

        tick();
        return setInterval(tick, 1000);
    }

    function startRefreshCountdown(options) {
        const settings = options || {};
        const countdownElement = document.getElementById(settings.elementId || "refresh-countdown");

        if (!countdownElement) {
            return null;
        }

        let refreshSeconds = settings.seconds || 30;

        function tick() {
            countdownElement.textContent = refreshSeconds + "s";

            refreshSeconds = refreshSeconds - 1;

            if (refreshSeconds < 0) {
                if (typeof settings.onComplete === "function") {
                    settings.onComplete();
                } else {
                    window.location.reload();
                }
            }
        }

        tick();
        return setInterval(tick, 1000);
    }

    function startDashboardTimers(options) {
        const settings = options || {};

        function tick() {
            updateElementFromCadTime(settings.lastUpdatedElementId || "last-updated", {
                timeOnly: true
            });

            updateCallElapsedTimers();
            updateLocalTime(settings.localTimeElementId || "dashboard-local-time");
        }

        tick();

        const intervalId = setInterval(tick, 1000);

        const countdownId = startRefreshCountdown({
            elementId: settings.refreshElementId || "refresh-countdown",
            seconds: settings.refreshSeconds || 30,
            onComplete: settings.onRefreshComplete
        });

        return {
            intervalId: intervalId,
            countdownId: countdownId
        };
    }

    function startCallDetailTimers() {
        function tick() {
            updateIncidentElapsed();
            updateUnitStatusTimers();
            updateCadDisplayTimes();
        }

        tick();

        return setInterval(tick, 1000);
    }

    function startUnitsBoardTimers() {
        function tick() {
            updateCadDisplayTimes();
            updateUnitStatusTimers();
        }

        tick();

        return setInterval(tick, 1000);
    }

    return {
        parseCadDateTime: parseCadDateTime,
        formatElapsedTime: formatElapsedTime,
        formatCadDisplayTime: formatCadDisplayTime,
        updateCadDisplayTimes: updateCadDisplayTimes,
        updateElementFromCadTime: updateElementFromCadTime,
        updateElapsedElements: updateElapsedElements,
        updateIncidentElapsed: updateIncidentElapsed,
        updateCallElapsedTimers: updateCallElapsedTimers,
        updateUnitStatusTimers: updateUnitStatusTimers,
        updateLocalTime: updateLocalTime,
        startClock: startClock,
        startRefreshCountdown: startRefreshCountdown,
        startDashboardTimers: startDashboardTimers,
        startCallDetailTimers: startCallDetailTimers,
        startUnitsBoardTimers: startUnitsBoardTimers
    };
})();