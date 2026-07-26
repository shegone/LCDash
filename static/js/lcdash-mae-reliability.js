(function () {
    "use strict";

    document.querySelectorAll("[data-run-evaluation]").forEach(function (button) {
        button.addEventListener("click", async function () {
            const caseId = button.dataset.runEvaluation;
            const row = document.querySelector(`[data-evaluation-case="${caseId}"]`);
            const result = row ? row.querySelector(".case-result") : null;
            button.disabled = true;
            if (result) {
                result.textContent = "Running…";
                result.className = "case-result";
            }
            try {
                const response = await fetch("/api/mae/evaluations/run", {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    cache: "no-store",
                    body: JSON.stringify({case_id: caseId})
                });
                const payload = await response.json();
                if (!response.ok) throw new Error(payload.detail || "Test failed.");
                if (result) {
                    result.textContent = `${payload.passed ? "PASS" : "FAIL"} · ${payload.duration_ms} ms · ${(payload.actual_source_kinds || []).join(", ") || "policy"}`;
                    result.classList.add(payload.passed ? "is-pass" : "is-fail");
                    result.title = payload.answer || payload.error || "";
                }
            } catch (error) {
                if (result) {
                    result.textContent = error.message || "Test failed";
                    result.classList.add("is-fail");
                }
            } finally {
                button.disabled = false;
            }
        });
    });

    const memoryForm = document.getElementById("memory-form");
    if (memoryForm) {
        memoryForm.addEventListener("submit", async function (event) {
            event.preventDefault();
            const formData = new FormData(memoryForm);
            const status = document.getElementById("memory-form-status");
            if (status) status.textContent = "Saving…";
            try {
                const response = await fetch("/api/mae/memory", {
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
                if (!response.ok) throw new Error(payload.detail || "Could not save memory.");
                window.location.reload();
            } catch (error) {
                if (status) status.textContent = error.message || "Could not save memory.";
            }
        });
    }

    document.querySelectorAll("[data-review-memory]").forEach(function (button) {
        button.addEventListener("click", async function () {
            button.disabled = true;
            try {
                const response = await fetch("/api/mae/memory/review", {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    cache: "no-store",
                    body: JSON.stringify({
                        memory_id: Number(button.dataset.reviewMemory),
                        decision: button.dataset.decision
                    })
                });
                const payload = await response.json();
                if (!response.ok) throw new Error(payload.detail || "Review failed.");
                window.location.reload();
            } catch (error) {
                button.disabled = false;
                window.alert(error.message || "Review failed.");
            }
        });
    });
})();
