(function () {
    "use strict";

    const testButton = document.getElementById("nga-test-disruption");
    const alertBox = document.getElementById("nga-push-alert");
    const dismissButton = document.getElementById("nga-dismiss-alert");

    function showVisualSimulation() {
        const path = document.querySelector('[data-path-id="verizon-fiber"]');
        if (path) {
            path.classList.remove("healthy");
            path.classList.add("critical", "nga-path-testing");
            const state = path.querySelector(".nga-path-state b");
            if (state) state.textContent = "SIMULATED INTERRUPTION";
            window.setTimeout(function () {
                path.classList.remove("nga-path-testing");
            }, 7000);
        }
        if (alertBox) alertBox.hidden = false;
    }

    testButton?.addEventListener("click", showVisualSimulation);
    dismissButton?.addEventListener("click", function () {
        if (alertBox) alertBox.hidden = true;
    });
    document.querySelectorAll("[data-cad-time]").forEach(function (element) {
        if (window.LCDashTime) {
            element.textContent = window.LCDashTime.formatCadDisplayTime(element.dataset.cadTime);
        }
    });
}());
