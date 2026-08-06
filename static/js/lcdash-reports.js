(function () {
    "use strict";

    const form = document.getElementById("county-report-form");
    const monthInput = document.getElementById("county-report-month");
    const runButton = document.getElementById("county-report-run");
    const progress = document.getElementById("county-report-progress");
    const progressTitle = document.getElementById("county-report-progress-title");
    const progressDetail = document.getElementById("county-report-progress-detail");
    const errorPanel = document.getElementById("county-report-error");
    const output = document.getElementById("county-report-output");
    const pdfLink = document.getElementById("county-report-pdf");
    let activeJobId = "";

    function previousMonth() {
        const date = new Date();
        date.setDate(1);
        date.setMonth(date.getMonth() - 1);
        return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
    }

    function number(value) {
        return Number(value || 0).toLocaleString();
    }

    function fillRows(targetId, rows) {
        const target = document.getElementById(targetId);
        target.replaceChildren();
        (rows || []).forEach(function (row) {
            const tr = document.createElement("tr");
            const department = document.createElement("td");
            const runs = document.createElement("td");
            department.textContent = row.department || "Unknown";
            runs.textContent = number(row.runs);
            tr.append(department, runs);
            target.appendChild(tr);
        });
    }

    function renderReport(report, jobId) {
        fillRows("county-report-fire", report.fire);
        fillRows("county-report-law", report.law);
        fillRows("county-report-ems", report.ems);
        document.getElementById("county-report-fire-total").textContent = number(report.fire_total);
        document.getElementById("county-report-law-total").textContent = number(report.law_total);
        document.getElementById("county-report-leasa-total").textContent = number(report.leasa_total);
        document.getElementById("county-report-period").textContent = report.month_label || report.month;
        document.getElementById("county-report-source").textContent = "Source: CentralSquare read-only assigned-unit runs";
        document.getElementById("county-report-generated").textContent = report.generated_at
            ? `Generated ${new Date(report.generated_at).toLocaleString("en-US", {timeZone: "America/New_York", timeZoneName: "short"})}`
            : "";
        pdfLink.href = `/api/reports/county-commission/jobs/${encodeURIComponent(jobId)}/pdf`;
        output.hidden = false;
        progress.hidden = true;
        output.scrollIntoView({behavior: "smooth", block: "start"});
    }

    async function pollJob(jobId) {
        activeJobId = jobId;
        while (activeJobId === jobId) {
            const response = await fetch(
                `/api/reports/county-commission/jobs/${encodeURIComponent(jobId)}`,
                {cache: "no-store"}
            );
            const job = await response.json();
            if (!response.ok) throw new Error(job.detail || "Report status is unavailable.");
            if (job.status === "complete") {
                renderReport(job.result, jobId);
                return;
            }
            if (job.status === "failed") {
                throw new Error(job.message || "The monthly report could not be completed.");
            }
            progressTitle.textContent = job.status === "queued"
                ? "Preparing monthly query…"
                : "Reading monthly CAD run activity…";
            progressDetail.textContent = job.pages_scanned
                ? `${number(job.pages_scanned)} pages reviewed · ${number(job.records_scanned)} records processed`
                : "This can take about two minutes.";
            await new Promise(function (resolve) { window.setTimeout(resolve, 1500); });
        }
    }

    form.addEventListener("submit", async function (event) {
        event.preventDefault();
        errorPanel.hidden = true;
        output.hidden = true;
        progress.hidden = false;
        runButton.disabled = true;
        progressTitle.textContent = "Starting monthly query…";
        progressDetail.textContent = "This can take about two minutes.";
        try {
            const response = await fetch("/api/reports/county-commission/jobs", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                cache: "no-store",
                body: JSON.stringify({month: monthInput.value})
            });
            const job = await response.json();
            if (!response.ok) throw new Error(job.detail || "The report could not be started.");
            await pollJob(job.job_id);
        } catch (error) {
            progress.hidden = true;
            errorPanel.textContent = error.message || "The monthly report could not be completed.";
            errorPanel.hidden = false;
        } finally {
            runButton.disabled = false;
        }
    });

    document.getElementById("county-report-print").addEventListener("click", function () {
        window.print();
    });

    monthInput.value = previousMonth();
})();
