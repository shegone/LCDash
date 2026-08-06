(function () {
    function readSnapshot() {
        const element = document.getElementById("analytics-snapshot");
        if (!element) {
            return null;
        }
        try {
            return JSON.parse(element.textContent);
        } catch (error) {
            return null;
        }
    }

    function chartColors() {
        return [
            "#4cc9ff",
            "#69ffb9",
            "#ffd66b",
            "#a78bfa",
            "#ff7b9c",
            "#5eead4",
            "#f59e0b",
            "#93c5fd"
        ];
    }

    function baseOptions() {
        return {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    labels: {
                        color: "#cfe6fb",
                        usePointStyle: true,
                        boxWidth: 8
                    }
                },
                tooltip: {
                    backgroundColor: "rgba(5, 16, 30, .96)",
                    titleColor: "#ffffff",
                    bodyColor: "#dbeeff",
                    borderColor: "rgba(76, 201, 255, .35)",
                    borderWidth: 1
                }
            },
            scales: {
                x: {
                    ticks: { color: "#8fb7d9", maxRotation: 0 },
                    grid: { color: "rgba(143, 183, 217, .08)" }
                },
                y: {
                    beginAtZero: true,
                    ticks: { color: "#8fb7d9", precision: 0 },
                    grid: { color: "rgba(143, 183, 217, .10)" }
                }
            }
        };
    }

    function renderDaily(snapshot) {
        const canvas = document.getElementById("daily-volume-chart");
        if (!canvas || !snapshot.daily_volume.length) {
            return;
        }
        new Chart(canvas, {
            type: "line",
            data: {
                labels: snapshot.daily_volume.map((row) => row.label),
                datasets: [{
                    label: "Calls",
                    data: snapshot.daily_volume.map((row) => row.count),
                    borderColor: "#4cc9ff",
                    backgroundColor: "rgba(76, 201, 255, .16)",
                    fill: true,
                    tension: .32,
                    pointRadius: snapshot.daily_volume.length > 45 ? 0 : 3,
                    pointHoverRadius: 5
                }]
            },
            options: baseOptions()
        });
    }

    function renderHourly(snapshot) {
        const canvas = document.getElementById("hourly-volume-chart");
        if (!canvas) {
            return;
        }
        const options = baseOptions();
        options.plugins.legend.display = false;
        new Chart(canvas, {
            type: "bar",
            data: {
                labels: snapshot.hourly_volume.map((row) => row.label),
                datasets: [{
                    data: snapshot.hourly_volume.map((row) => row.count),
                    backgroundColor: "rgba(105, 255, 185, .62)",
                    borderColor: "#69ffb9",
                    borderWidth: 1,
                    borderRadius: 5
                }]
            },
            options
        });
    }

    function renderAgencies(snapshot) {
        const canvas = document.getElementById("agency-mix-chart");
        if (!canvas || !snapshot.agency_mix.length) {
            return;
        }
        new Chart(canvas, {
            type: "doughnut",
            data: {
                labels: snapshot.agency_mix.map((row) => row.label),
                datasets: [{
                    data: snapshot.agency_mix.map((row) => row.count),
                    backgroundColor: chartColors(),
                    borderColor: "#0b1a2d",
                    borderWidth: 3
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: "64%",
                plugins: baseOptions().plugins
            }
        });
    }

    function renderStationDiscipline(snapshot) {
        const canvas = document.getElementById("station-discipline-chart");
        const rows = snapshot.station_discipline || [];
        if (!canvas || !rows.length) {
            return;
        }

        const chartWrap = canvas.closest(".station-chart-wrap");
        if (chartWrap) {
            chartWrap.style.height = Math.max(
                360,
                Math.min(640, rows.length * 28)
            ) + "px";
        }

        const options = baseOptions();
        options.indexAxis = "y";
        options.plugins.tooltip.callbacks = {
            label(context) {
                const value = context.parsed.x || 0;
                return `${context.dataset.label}: ${value} ${value === 1 ? "call" : "calls"}`;
            }
        };
        options.scales.x.stacked = false;
        options.scales.x.title = {
            display: true,
            text: "Distinct calls",
            color: "#8fb7d9"
        };
        options.scales.y.stacked = false;
        options.scales.y.ticks = {
            color: "#dbeeff",
            autoSkip: false
        };

        new Chart(canvas, {
            type: "bar",
            data: {
                labels: rows.map((row) => `${row.discipline} · ${row.station}`),
                datasets: [
                    {
                        label: "Law",
                        data: rows.map((row) => row.law),
                        backgroundColor: "rgba(76, 201, 255, .68)",
                        borderColor: "#4cc9ff",
                        borderWidth: 1,
                        borderRadius: 4
                    },
                    {
                        label: "EMS",
                        data: rows.map((row) => row.ems),
                        backgroundColor: "rgba(105, 255, 185, .68)",
                        borderColor: "#69ffb9",
                        borderWidth: 1,
                        borderRadius: 4
                    },
                    {
                        label: "Fire",
                        data: rows.map((row) => row.fire),
                        backgroundColor: "rgba(255, 123, 156, .72)",
                        borderColor: "#ff7b9c",
                        borderWidth: 1,
                        borderRadius: 4
                    }
                ]
            },
            options
        });
    }

    function renderDispatchers(snapshot) {
        const canvas = document.getElementById("dispatcher-workload-chart");
        const rows = snapshot.dispatchers || [];
        if (!canvas || !rows.length) {
            return;
        }

        const options = baseOptions();
        options.indexAxis = "y";
        options.plugins.legend.display = false;
        options.plugins.tooltip.callbacks = {
            label(context) {
                const value = context.parsed.x || 0;
                return `${value} ${value === 1 ? "call" : "calls"} entered`;
            }
        };
        options.scales.x.title = {
            display: true,
            text: "Completed calls",
            color: "#8fb7d9"
        };
        options.scales.y.ticks = {
            color: "#dbeeff",
            autoSkip: false
        };

        new Chart(canvas, {
            type: "bar",
            data: {
                labels: rows.map((row) => row.call_taker),
                datasets: [{
                    data: rows.map((row) => row.calls_entered),
                    backgroundColor: "rgba(167, 139, 250, .68)",
                    borderColor: "#a78bfa",
                    borderWidth: 1,
                    borderRadius: 5
                }]
            },
            options
        });
    }

    function renderWeekday(snapshot) {
        const canvas = document.getElementById("weekday-volume-chart");
        const rows = snapshot.weekday_volume || [];
        if (!canvas || !rows.length) {
            return;
        }
        const options = baseOptions();
        options.plugins.legend.display = false;
        new Chart(canvas, {
            type: "bar",
            data: {
                labels: rows.map((row) => row.label),
                datasets: [{
                    data: rows.map((row) => row.count),
                    backgroundColor: "rgba(167, 139, 250, .68)",
                    borderColor: "#a78bfa",
                    borderWidth: 1,
                    borderRadius: 5
                }]
            },
            options
        });
    }

    function savedWidgetRows(snapshot, viewKey) {
        const definitions = {
            daily_volume: ["daily_volume", "label", "count", "line"],
            hourly_volume: ["hourly_volume", "label", "count", "bar"],
            weekday_volume: ["weekday_volume", "label", "count", "bar"],
            agency_mix: ["agency_mix", "label", "count", "doughnut"],
            incident_types: ["incident_types", "label", "count", "bar"],
            dispatcher_workload: ["dispatchers", "call_taker", "calls_entered", "bar"],
            busiest_units: ["busiest_units", "unit_number", "responses", "bar"],
            busiest_stations: ["busiest_stations", "station", "calls", "bar"]
        };
        const definition = definitions[viewKey];
        if (!definition) return null;
        return {
            rows: (snapshot[definition[0]] || []).slice(0, 30),
            labelKey: definition[1],
            valueKey: definition[2],
            type: definition[3]
        };
    }

    function renderSavedWidgets(snapshot) {
        document.querySelectorAll("[data-saved-widget]").forEach((card) => {
            const config = savedWidgetRows(snapshot, card.dataset.viewKey);
            const canvas = card.querySelector("canvas");
            if (!config || !canvas || !config.rows.length) return;
            const options = baseOptions();
            if (config.type === "doughnut") {
                delete options.scales;
            } else {
                options.plugins.legend.display = false;
            }
            new Chart(canvas, {
                type: config.type,
                data: {
                    labels: config.rows.map((row) => row[config.labelKey]),
                    datasets: [{
                        label: "Calls",
                        data: config.rows.map((row) => row[config.valueKey]),
                        backgroundColor: config.type === "doughnut"
                            ? chartColors()
                            : "rgba(76, 201, 255, .58)",
                        borderColor: "#4cc9ff",
                        borderWidth: 1,
                        tension: .3,
                        fill: config.type === "line"
                    }]
                },
                options
            });
            const retire = card.querySelector("[data-retire-widget]");
            if (retire) retire.addEventListener("click", async () => {
                retire.disabled = true;
                const response = await fetch("/api/analytics/widgets/retire", {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    cache: "no-store",
                    body: JSON.stringify({widget_id: Number(card.dataset.widgetId)})
                });
                if (response.ok) card.remove();
                else retire.disabled = false;
            });
        });
    }

    function printAnalyticsTable(snapshot, sectionId, reportTitle) {
        const section = document.getElementById(sectionId);
        const table = section ? section.querySelector("table") : null;
        if (!table) {
            return;
        }

        const existingShell = document.getElementById("analytics-print-shell");
        if (existingShell) {
            existingShell.remove();
        }

        const printShell = document.createElement("div");
        printShell.id = "analytics-print-shell";

        const title = document.createElement("h1");
        title.textContent = reportTitle;
        printShell.appendChild(title);

        const subtitle = document.createElement("div");
        subtitle.className = "print-report-subtitle";
        const generatedAt = snapshot.generated_at
            ? new Date(snapshot.generated_at).toLocaleString("en-US", {timeZone: "America/New_York", timeZoneName: "short"})
            : new Date().toLocaleString("en-US", {timeZone: "America/New_York", timeZoneName: "short"});
        subtitle.textContent =
            `Logan County 911 Operations Analytics | ${snapshot.period_label} | Generated ${generatedAt}`;
        printShell.appendChild(subtitle);
        printShell.appendChild(table.cloneNode(true));

        document.body.appendChild(printShell);
        document.body.classList.add("analytics-printing");

        let cleanedUp = false;
        function cleanupPrintView() {
            if (cleanedUp) {
                return;
            }
            cleanedUp = true;
            document.body.classList.remove("analytics-printing");
            printShell.remove();
        }

        window.addEventListener("afterprint", cleanupPrintView, { once: true });
        window.print();
        window.setTimeout(cleanupPrintView, 30000);
    }

    function bindPrintButtons(snapshot) {
        document.querySelectorAll("[data-print-analytics]").forEach((button) => {
            if (button.dataset.printReady === "true") {
                return;
            }
            button.dataset.printReady = "true";
            button.addEventListener("click", () => {
                printAnalyticsTable(
                    snapshot,
                    button.dataset.printAnalytics,
                    button.dataset.printTitle || "Operations Analytics"
                );
            });
        });
    }

    function start() {
        const snapshot = readSnapshot();
        if (!snapshot || !snapshot.available) {
            return;
        }
        bindPrintButtons(snapshot);
        if (typeof Chart === "undefined") {
            document.querySelectorAll(".chart-wrap, .station-chart-wrap")
                .forEach((chartWrap) => {
                    chartWrap.style.display = "none";
                    const message = document.createElement("div");
                    message.className = "text-secondary py-4";
                    message.textContent = "Chart display is temporarily unavailable.";
                    chartWrap.insertAdjacentElement("afterend", message);
                });
            return;
        }
        Chart.defaults.font.family = '"Segoe UI", Arial, sans-serif';
        Chart.defaults.color = "#cfe6fb";
        renderDaily(snapshot);
        renderHourly(snapshot);
        renderWeekday(snapshot);
        renderAgencies(snapshot);
        renderDispatchers(snapshot);
        renderStationDiscipline(snapshot);
        renderSavedWidgets(snapshot);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", start);
    } else {
        start();
    }
})();
