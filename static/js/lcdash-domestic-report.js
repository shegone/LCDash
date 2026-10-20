(function () {
    "use strict";

    const form = document.getElementById("domestic-report-form");
    if (!form) return;

    const runButton = document.getElementById("domestic-report-run");
    const progress = document.getElementById("domestic-report-progress");
    const progressTitle = document.getElementById("domestic-report-progress-title");
    const progressDetail = document.getElementById("domestic-report-progress-detail");
    const errorPanel = document.getElementById("domestic-report-error");
    const output = document.getElementById("domestic-report-output");
    let activeJobId = "";

    function number(value) {
        return Number(value || 0).toLocaleString();
    }

    function percent(value) {
        return value === null || value === undefined ? "-" : `${Number(value).toFixed(2)}%`;
    }

    function cell(text) {
        const td = document.createElement("td");
        td.textContent = text;
        return td;
    }

    function fillCodes(targetId, rows, emptyText) {
        const target = document.getElementById(targetId);
        target.replaceChildren();
        if (!rows || !rows.length) {
            const tr = document.createElement("tr");
            const td = cell(emptyText);
            td.colSpan = 2;
            tr.appendChild(td);
            target.appendChild(tr);
            return;
        }
        rows.forEach(function (row) {
            const tr = document.createElement("tr");
            tr.append(cell(row.code), cell(number(row.calls)));
            target.appendChild(tr);
        });
    }

    function renderReport(report) {
        const body = document.getElementById("domestic-report-months");
        body.replaceChildren();
        (report.months || []).forEach(function (month) {
            const tr = document.createElement("tr");
            const label = month.in_progress ? `${month.month_label} (so far)` : month.month_label;
            tr.append(
                cell(label),
                cell(number(month.total_calls)),
                cell(number(month.domestic_calls)),
                cell(percent(month.percent_domestic))
            );
            body.appendChild(tr);
        });

        document.getElementById("domestic-report-total").textContent = number(report.total_calls);
        document.getElementById("domestic-report-domestic").textContent = number(report.domestic_calls);
        document.getElementById("domestic-report-total-percent").textContent = percent(report.percent_domestic);
        document.getElementById("domestic-report-percent").textContent = percent(report.percent_domestic);
        document.getElementById("domestic-report-headline-detail").textContent =
            `${number(report.domestic_calls)} of ${number(report.total_calls)} calls were domestic-related`;
        document.getElementById("domestic-report-period").textContent =
            `${report.from} through ${report.through}`;

        fillCodes("domestic-report-matched", report.matched_codes, "No call type matched.");
        fillCodes("domestic-report-other", report.other_codes, "None.");

        const reused = (report.quality || {}).months_reused_from_earlier_run || 0;
        document.getElementById("domestic-report-source").textContent =
            "Source: CentralSquare read-only call search; counted by " + report.counted_by +
            " containing “" + (report.match_terms || []).join("”, “") + "”" +
            (reused ? ` · ${reused} finished month${reused === 1 ? "" : "s"} reused from an earlier run` : "");
        document.getElementById("domestic-report-generated").textContent = report.generated_at
            ? `Generated ${new Date(report.generated_at).toLocaleString("en-US", {timeZone: "America/New_York", timeZoneName: "short"})}`
            : "";

        output.hidden = false;
        progress.hidden = true;
        output.scrollIntoView({behavior: "smooth", block: "start"});
    }

    async function pollJob(jobId) {
        activeJobId = jobId;
        while (activeJobId === jobId) {
            const response = await fetch(
                `/api/reports/domestic-calls/jobs/${encodeURIComponent(jobId)}`,
                {cache: "no-store"}
            );
            const job = await response.json();
            if (!response.ok) throw new Error(job.detail || "Report status is unavailable.");
            if (job.status === "complete") {
                renderReport(job.result);
                return;
            }
            if (job.status === "failed") {
                throw new Error(job.message || "The report could not be completed.");
            }
            progressTitle.textContent = job.status === "queued"
                ? "Preparing query…"
                : `Reading ${job.current_month || "calls"}…`;
            progressDetail.textContent = job.pages_scanned
                ? `${number(job.pages_scanned)} pages reviewed · ${number(job.records_scanned)} calls counted`
                : "This can take several minutes the first time.";
            await new Promise(function (resolve) { window.setTimeout(resolve, 2000); });
        }
    }

    form.addEventListener("submit", async function (event) {
        event.preventDefault();
        errorPanel.hidden = true;
        output.hidden = true;
        progress.hidden = false;
        runButton.disabled = true;
        progressTitle.textContent = "Starting query…";
        progressDetail.textContent = "This can take several minutes the first time.";
        try {
            const response = await fetch("/api/reports/domestic-calls/jobs", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                cache: "no-store",
                body: "{}"
            });
            const job = await response.json();
            if (!response.ok) throw new Error(job.detail || "The report could not be started.");
            await pollJob(job.job_id);
        } catch (error) {
            progress.hidden = true;
            errorPanel.textContent = error.message || "The report could not be completed.";
            errorPanel.hidden = false;
        } finally {
            runButton.disabled = false;
        }
    });

    document.getElementById("domestic-report-print").addEventListener("click", function () {
        document.body.classList.add("print-domestic");
        const done = function () { document.body.classList.remove("print-domestic"); };
        window.addEventListener("afterprint", done, {once: true});
        window.print();
    });
})();
