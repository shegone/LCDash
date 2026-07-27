(function () {
    "use strict";

    const dataElement = document.getElementById("map-data");
    const mapElement = document.getElementById("operations-map");
    if (!dataElement || !mapElement) {
        return;
    }
    if (typeof L === "undefined") {
        mapElement.setAttribute("role", "alert");
        mapElement.textContent = "The map viewer could not be loaded. Refresh this page to try again.";
        return;
    }

    let mapData;
    try {
        mapData = JSON.parse(dataElement.textContent);
    } catch (error) {
        mapElement.setAttribute("role", "alert");
        mapElement.textContent = "Map data could not be loaded.";
        return;
    }

    const defaultCenter = [37.8487, -81.9935];
    const map = L.map("operations-map", { zoomControl: true }).setView(defaultCenter, 11);

    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: "&copy; OpenStreetMap contributors"
    }).addTo(map);

    const records = [];
    const callToggle = document.getElementById("show-calls");
    const unitToggle = document.getElementById("show-units");
    const agencyFilter = document.getElementById("map-agency-filter");
    const priorityFilter = document.getElementById("map-priority-filter");
    const statusFilter = document.getElementById("map-status-filter");
    const visibleCount = document.getElementById("visible-marker-count");

    function addTextRow(container, label, value) {
        if (value === null || value === undefined || value === "") {
            return;
        }

        const row = document.createElement("div");
        row.className = "map-popup-row";

        const labelElement = document.createElement("span");
        labelElement.textContent = label;
        const valueElement = document.createElement("strong");
        valueElement.textContent = String(value);

        row.append(labelElement, valueElement);
        container.appendChild(row);
    }

    function createPopup(properties) {
        const container = document.createElement("div");
        container.className = "lcdash-map-popup";

        const heading = document.createElement("div");
        heading.className = "map-popup-title";
        heading.textContent = properties.kind === "call"
            ? (properties.incident_description || properties.incident_code || "Active Call")
            : (properties.unit_number || "CAD Unit");
        container.appendChild(heading);

        if (properties.kind === "call") {
            addTextRow(container, "CFS", properties.cfs_number);
            addTextRow(container, "Incident", properties.incident_code);
            addTextRow(container, "Priority", properties.priority);
            addTextRow(container, "Agency", properties.agency);
            addTextRow(container, "Status", properties.status);
            addTextRow(container, "Location", properties.location_label);
        } else {
            addTextRow(container, "Status", properties.status);
            addTextRow(container, "Agency", properties.agency);
            addTextRow(container, "Type", properties.unit_type);
            addTextRow(container, "CFS", properties.cfs_number);
            addTextRow(container, "Location age", formatAge(properties.location_age_seconds));
            addTextRow(container, "Location source", properties.location_source);
        }

        if (properties.detail_url && String(properties.detail_url).startsWith("/calls/")) {
            const link = document.createElement("a");
            link.className = "map-popup-link";
            link.href = properties.detail_url;
            link.target = "_blank";
            link.rel = "noopener";
            link.textContent = "Open Incident Command View";
            container.appendChild(link);
        }

        return container;
    }

    function formatAge(seconds) {
        if (seconds === null || seconds === undefined || Number.isNaN(Number(seconds))) {
            return "Unknown";
        }

        const totalSeconds = Math.max(0, Number(seconds));
        if (totalSeconds < 60) {
            return Math.round(totalSeconds) + " sec";
        }

        return Math.floor(totalSeconds / 60) + " min";
    }

    function callColor(priority) {
        const value = Number(priority);
        if (value > 0 && value <= 10) return "#ff4c4c";
        if (value === 15) return "#ffd66b";
        if (value === 20) return "#4cc9ff";
        return "#69ffb9";
    }

    function unitColor(status) {
        const value = String(status || "").toLowerCase();
        if (value.includes("transport")) return "#da7dff";
        if (value.includes("scene") || value.includes("arriv")) return "#ffd66b";
        if (value.includes("route")) return "#4cc9ff";
        if (value.includes("available") || value.includes("station")) return "#69ffb9";
        return "#dbeeff";
    }

    function createMarker(feature) {
        if (!feature.geometry || feature.geometry.type !== "Point") {
            return null;
        }

        const coordinates = feature.geometry.coordinates || [];
        const longitude = Number(coordinates[0]);
        const latitude = Number(coordinates[1]);
        if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) {
            return null;
        }

        const properties = feature.properties || {};
        let marker;
        if (properties.kind === "call") {
            const color = callColor(properties.priority);
            marker = L.circleMarker([latitude, longitude], {
                radius: 11,
                color: "#ffffff",
                weight: 2,
                fillColor: color,
                fillOpacity: 0.9
            });
        } else {
            const color = unitColor(properties.status_group || properties.status);
            marker = L.marker([latitude, longitude], {
                icon: L.divIcon({
                    className: "lcdash-unit-marker-shell",
                    html: '<span class="lcdash-unit-marker" style="--unit-color:' + color + '"></span>',
                    iconSize: [26, 26],
                    iconAnchor: [13, 13]
                })
            });
        }

        marker.bindPopup(createPopup(properties));
        return { marker: marker, properties: properties };
    }

    function populateFilter(selectElement, values, firstLabel) {
        if (!selectElement) return;

        selectElement.textContent = "";
        const firstOption = document.createElement("option");
        firstOption.value = "";
        firstOption.textContent = firstLabel;
        selectElement.appendChild(firstOption);

        Array.from(values).filter(Boolean).sort().forEach(function (value) {
            const option = document.createElement("option");
            option.value = value;
            option.textContent = value;
            selectElement.appendChild(option);
        });
    }

    (mapData.features || []).forEach(function (feature) {
        const record = createMarker(feature);
        if (record) records.push(record);
    });

    populateFilter(
        agencyFilter,
        new Set(records.map(function (record) { return record.properties.agency; })),
        "All agencies"
    );
    populateFilter(
        priorityFilter,
        new Set(records.filter(function (record) { return record.properties.kind === "call"; })
            .map(function (record) { return record.properties.priority; })),
        "All priorities"
    );
    populateFilter(
        statusFilter,
        new Set(records.map(function (record) { return record.properties.status_group || record.properties.status; })),
        "All statuses"
    );

    function recordVisible(record) {
        const properties = record.properties;
        if (properties.kind === "call" && callToggle && !callToggle.checked) return false;
        if (properties.kind === "unit" && unitToggle && !unitToggle.checked) return false;
        if (agencyFilter && agencyFilter.value && properties.agency !== agencyFilter.value) return false;
        if (priorityFilter && priorityFilter.value && properties.priority !== priorityFilter.value) return false;
        if (statusFilter && statusFilter.value) {
            const status = properties.status_group || properties.status || "";
            if (status !== statusFilter.value) return false;
        }
        return true;
    }

    function applyFilters(fitMap) {
        const visibleMarkers = [];
        records.forEach(function (record) {
            if (recordVisible(record)) {
                if (!map.hasLayer(record.marker)) record.marker.addTo(map);
                visibleMarkers.push(record.marker);
            } else if (map.hasLayer(record.marker)) {
                map.removeLayer(record.marker);
            }
        });

        if (visibleCount) visibleCount.textContent = String(visibleMarkers.length);
        if (fitMap && visibleMarkers.length) {
            const bounds = L.featureGroup(visibleMarkers).getBounds();
            if (bounds.isValid()) map.fitBounds(bounds.pad(0.15), { maxZoom: 15 });
        }
    }

    [callToggle, unitToggle, agencyFilter, priorityFilter, statusFilter].forEach(function (control) {
        if (control) control.addEventListener("change", function () { applyFilters(false); });
    });

    const clearButton = document.getElementById("clear-map-filters");
    if (clearButton) {
        clearButton.addEventListener("click", function () {
            if (callToggle) callToggle.checked = true;
            if (unitToggle) unitToggle.checked = true;
            if (agencyFilter) agencyFilter.value = "";
            if (priorityFilter) priorityFilter.value = "";
            if (statusFilter) statusFilter.value = "";
            applyFilters(true);
        });
    }

    const fitButton = document.getElementById("fit-visible-markers");
    if (fitButton) fitButton.addEventListener("click", function () { applyFilters(true); });

    applyFilters(true);
})();
