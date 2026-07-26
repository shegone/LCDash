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

    function start() {
        const snapshot = readSnapshot();
        if (!snapshot || !snapshot.available || typeof Chart === "undefined") {
            return;
        }
        Chart.defaults.font.family = '"Segoe UI", Arial, sans-serif';
        Chart.defaults.color = "#cfe6fb";
        renderDaily(snapshot);
        renderHourly(snapshot);
        renderAgencies(snapshot);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", start);
    } else {
        start();
    }
})();
