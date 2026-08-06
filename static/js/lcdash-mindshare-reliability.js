(function () {
    "use strict";

    if (document.getElementById("jack-reliability-unavailable")) return;

    async function runCase(button) {
        const caseId = button.dataset.runJack;
        const row = document.querySelector(`[data-jack-case="${caseId}"]`);
        const resultCell = row ? row.querySelector(".jack-case-result") : null;
        button.disabled = true;
        if (resultCell) {
            resultCell.textContent = "JACK is checking the manuals…";
            resultCell.className = "jack-case-result";
        }
        try {
            const controller = new AbortController();
            const timeoutId = window.setTimeout(function () {
                controller.abort();
            }, 125000);
            const response = await fetch("/api/mindshare/evaluations/run", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                cache: "no-store",
                signal: controller.signal,
                body: JSON.stringify({case_id: caseId})
            });
            window.clearTimeout(timeoutId);
            const payload = await response.json();
            if (!response.ok) throw new Error(payload.detail || "Test failed.");
            if (resultCell) {
                const seconds = (Number(payload.duration_ms || 0) / 1000).toFixed(1);
                const source = (payload.actual_documents || [])[0] || "Safety boundary";
                resultCell.textContent = `${payload.passed ? "PASS" : "REVIEW"} · ${seconds}s · ${source}`;
                resultCell.classList.add(payload.passed ? "is-pass" : "is-fail");
                resultCell.title = payload.answer || payload.error || "";
            }
        } catch (error) {
            if (resultCell) {
                resultCell.textContent = error.name === "AbortError"
                    ? "Timed out after 125 seconds"
                    : (error.message || "Test failed");
                resultCell.classList.add("is-fail");
            }
        } finally {
            button.disabled = false;
        }
    }

    const buttons = Array.from(document.querySelectorAll("[data-run-jack]"));
    buttons.forEach(function (button) {
        button.addEventListener("click", function () {
            runCase(button);
        });
    });

    const runAll = document.getElementById("run-visible-jack-tests");
    if (runAll) {
        runAll.addEventListener("click", async function () {
            runAll.disabled = true;
            runAll.textContent = "Running tests in sequence…";
            for (const button of buttons) {
                await runCase(button);
            }
            runAll.disabled = false;
            runAll.innerHTML = '<i class="bi bi-arrow-repeat"></i> Run all 30 again';
        });
    }

    const memoryForm = document.getElementById("jack-memory-form");
    if (memoryForm) {
        memoryForm.addEventListener("submit", async function (event) {
            event.preventDefault();
            const formData = new FormData(memoryForm);
            const status = document.getElementById("jack-memory-form-status");
            if (status) status.textContent = "Saving...";
            try {
                const response = await fetch("/api/mindshare/memory", {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    cache: "no-store",
                    body: JSON.stringify({
                        title: formData.get("title"),
                        trigger_text: formData.get("trigger_text"),
                        guidance: formData.get("guidance"),
                        source_interaction_id: ""
                    })
                });
                const payload = await response.json();
                if (!response.ok) throw new Error(payload.detail || "Could not save JACK knowledge.");
                window.location.reload();
            } catch (error) {
                if (status) status.textContent = error.message || "Could not save JACK knowledge.";
            }
        });
    }

    document.querySelectorAll("[data-review-jack-memory]").forEach(function (button) {
        button.addEventListener("click", async function () {
            button.disabled = true;
            try {
                const response = await fetch("/api/mindshare/memory/review", {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    cache: "no-store",
                    body: JSON.stringify({
                        memory_id: Number(button.dataset.reviewJackMemory),
                        decision: button.dataset.decision
                    })
                });
                const payload = await response.json();
                if (!response.ok) throw new Error(payload.detail || "JACK knowledge review failed.");
                window.location.reload();
            } catch (error) {
                button.disabled = false;
                window.alert(error.message || "JACK knowledge review failed.");
            }
        });
    });
})();
