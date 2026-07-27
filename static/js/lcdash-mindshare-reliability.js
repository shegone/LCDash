(function () {
    "use strict";

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
})();
