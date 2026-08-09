// Saved MAE report templates on the Pre-Built Reports page (cloud pilot only).
//
// Reads GET /api/cloud-ai/reports/templates -- the endpoint that existed with
// no caller while "Save as Template" quietly wrote templates nobody could see
// again. Export deliberately goes preview-first: the backend requires
// preview_confirmed=true, so the user always sees what a template resolves to
// (row count, source, freshness) before a PDF is produced from it.
(function () {
    "use strict";

    const loading = document.getElementById("saved-templates-loading");
    const errorBox = document.getElementById("saved-templates-error");
    const emptyBox = document.getElementById("saved-templates-empty");
    const list = document.getElementById("saved-templates-list");
    if (!loading || !errorBox || !emptyBox || !list) return;

    const METRIC_LABELS = {
        call_count: "Call count",
        calls_by_nature: "Calls by nature",
        calls_by_hour: "Calls by hour",
        calls_by_agency: "Calls by agency",
        average_response_seconds: "Average response time",
        unit_commitment_minutes: "Unit commitment minutes",
    };
    const PERIOD_LABELS = {
        "24h": "Last 24 hours",
        "7d": "Last 7 days",
        "30d": "Last 30 days",
        "90d": "Last 90 days",
        "365d": "Last 365 days",
    };

    function showError(prefix, status, detail) {
        errorBox.hidden = false;
        const suffix = status ? ` (HTTP ${status}${detail ? `: ${detail}` : ""})` : "";
        errorBox.textContent = `${prefix}${suffix}`;
    }

    async function readDetail(response) {
        try {
            const body = await response.json();
            return typeof body.detail === "string" ? body.detail : "";
        } catch (error) {
            return "";
        }
    }

    function intentSummary(intent) {
        const metric = METRIC_LABELS[intent.metric] || intent.metric;
        const period = PERIOD_LABELS[intent.period] || intent.period;
        return `${metric} · ${period}`;
    }

    function safeFilename(title) {
        const cleaned = String(title).replace(/[^A-Za-z0-9 _-]/g, "").trim().replace(/\s+/g, "-");
        return `${cleaned || "mae-report"}.pdf`;
    }

    function renderTemplate(record) {
        const item = document.createElement("li");
        item.className = "saved-template glass";

        const head = document.createElement("div");
        head.className = "saved-template-head";
        const title = document.createElement("h3");
        title.textContent = record.title;
        const summary = document.createElement("span");
        summary.className = "saved-template-summary";
        summary.textContent = intentSummary(record.intent);
        head.append(title, summary);

        const meta = document.createElement("div");
        meta.className = "saved-template-meta";
        const created = window.LCDashTime
            ? window.LCDashTime.formatCadDisplayTime(record.created_at)
            : record.created_at;
        meta.textContent =
            `Saved by ${record.author_subject} · ${created} · ` +
            `visible to ${record.visible_to_roles.join(", ")}`;

        const preview = document.createElement("div");
        preview.className = "saved-template-preview";
        preview.hidden = true;

        const actions = document.createElement("div");
        actions.className = "saved-template-actions";
        const previewButton = document.createElement("button");
        previewButton.type = "button";
        previewButton.innerHTML = '<i class="bi bi-eye"></i> Preview';

        previewButton.addEventListener("click", async function () {
            previewButton.disabled = true;
            preview.hidden = false;
            preview.textContent = "Previewing…";
            try {
                const response = await fetch("/api/cloud-ai/reports/preview", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(record.intent),
                });
                if (!response.ok) {
                    const detail = await readDetail(response);
                    preview.textContent =
                        `Preview failed (HTTP ${response.status}${detail ? `: ${detail}` : ""}).`;
                    return;
                }
                const payload = await response.json();
                preview.textContent = "";

                const facts = document.createElement("p");
                const freshness = payload.freshness && window.LCDashTime
                    ? window.LCDashTime.formatCadDisplayTime(payload.freshness)
                    : (payload.freshness || "freshness unavailable");
                facts.textContent =
                    `${payload.rows.length} row${payload.rows.length === 1 ? "" : "s"} · ` +
                    `${payload.source} · ${freshness}. ${payload.disclaimer || ""}`;
                preview.appendChild(facts);

                if (!payload.rows.length) {
                    const none = document.createElement("p");
                    none.textContent =
                        "This template currently resolves to no rows, so a PDF would be empty.";
                    preview.appendChild(none);
                    return;
                }

                const exportButton = document.createElement("button");
                exportButton.type = "button";
                exportButton.innerHTML = '<i class="bi bi-file-earmark-pdf"></i> Download PDF';
                exportButton.addEventListener("click", async function () {
                    exportButton.disabled = true;
                    try {
                        const exportResponse = await fetch("/api/cloud-ai/reports/export", {
                            method: "POST",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({
                                intent: payload.intent,
                                preview_confirmed: true,
                            }),
                        });
                        if (!exportResponse.ok) {
                            preview.appendChild(document.createElement("p")).textContent =
                                `Export failed (HTTP ${exportResponse.status}).`;
                            return;
                        }
                        const blob = await exportResponse.blob();
                        const url = URL.createObjectURL(blob);
                        const link = document.createElement("a");
                        link.href = url;
                        link.download = safeFilename(record.title);
                        document.body.appendChild(link);
                        link.click();
                        link.remove();
                        URL.revokeObjectURL(url);
                    } finally {
                        exportButton.disabled = false;
                    }
                });
                preview.appendChild(exportButton);
            } finally {
                previewButton.disabled = false;
            }
        });

        actions.appendChild(previewButton);
        item.append(head, meta, actions, preview);
        return item;
    }

    async function load() {
        try {
            const response = await fetch("/api/cloud-ai/reports/templates", {
                headers: { "Accept": "application/json" },
            });
            loading.hidden = true;
            if (!response.ok) {
                showError(
                    "Saved templates could not be loaded",
                    response.status,
                    await readDetail(response)
                );
                return;
            }
            const payload = await response.json();
            const templates = Array.isArray(payload.templates) ? payload.templates : [];
            if (!templates.length) {
                emptyBox.hidden = false;
                return;
            }
            list.hidden = false;
            templates.forEach(function (record) {
                list.appendChild(renderTemplate(record));
            });
        } catch (error) {
            loading.hidden = true;
            showError("Saved templates could not be loaded (network error)");
        }
    }

    load();
})();
