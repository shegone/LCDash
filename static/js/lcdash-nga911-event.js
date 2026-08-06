(function () {
    "use strict";

    const button = document.getElementById("nga-acknowledge-event");
    const state = document.getElementById("nga-acknowledgment-state");
    if (!button || !state) return;

    const storageKey = `nga911Acknowledged:${button.dataset.eventId}`;

    function showAcknowledged(at) {
        const reviewedAt = new Date(at);
        const displayTime = Number.isNaN(reviewedAt.getTime())
            ? "This event was reviewed"
            : `Reviewed ${reviewedAt.toLocaleString()}`;
        state.classList.add("acknowledged");
        state.innerHTML = `<i class="bi bi-check-circle-fill"></i><div><strong>Simulation marked reviewed</strong><span>${displayTime}. This browser marker is not an operational acknowledgment.</span></div>`;
        button.disabled = true;
        button.innerHTML = '<i class="bi bi-check2-circle"></i> Simulation reviewed';
    }

    const priorAcknowledgment = localStorage.getItem(storageKey);
    if (priorAcknowledgment) showAcknowledged(priorAcknowledgment);

    button.addEventListener("click", function () {
        const acknowledgedAt = new Date().toISOString();
        localStorage.setItem(storageKey, acknowledgedAt);
        showAcknowledged(acknowledgedAt);
    });
}());
