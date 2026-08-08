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

    const referenceLayerControl = L.control.layers(null, null, {
        collapsed: window.matchMedia("(max-width: 767px)").matches,
        position: "topright"
    }).addTo(map);

    function referenceLayerStyle(layerId) {
        const styles = {
            county: { color: "#ffffff", weight: 2.5, fill: false, opacity: 0.9 },
            psap: { color: "#ffd66b", weight: 2, fill: false, dashArray: "7 5", opacity: 0.95 },
            municipalities: { color: "#a8a0ff", weight: 1.5, fillColor: "#a8a0ff", fillOpacity: 0.06 },
            provisioning: { color: "#4cc9ff", weight: 1.5, fill: false, dashArray: "4 5" },
            "esb-fire": { color: "#ff6f6f", weight: 1.3, fillColor: "#ff6f6f", fillOpacity: 0.08 },
            "esb-ems": { color: "#69ffb9", weight: 1.3, fillColor: "#69ffb9", fillOpacity: 0.08 },
            "esb-law": { color: "#4cc9ff", weight: 1.3, fillColor: "#4cc9ff", fillOpacity: 0.08 },
            roads: { color: "#8fb7d9", weight: 1.15, opacity: 0.62 }
        };
        return styles[layerId] || { color: "#dbeeff", weight: 1.25, fillOpacity: 0.05 };
    }

    function referenceLayerLabel(properties) {
        if (!properties) return "";
        return properties.name || properties.agency || "";
    }

    async function loadReferenceLayers() {
        try {
            const catalogResponse = await fetch("/api/operations/map/reference", {
                credentials: "same-origin"
            });
            if (!catalogResponse.ok) return;
            const catalog = await catalogResponse.json();
            const layers = Array.isArray(catalog.layers) ? catalog.layers : [];

            await Promise.all(layers.map(async function (layerInfo) {
                const layerResponse = await fetch(
                    "/api/operations/map/reference/" + encodeURIComponent(layerInfo.id),
                    { credentials: "same-origin" }
                );
                if (!layerResponse.ok) return;
                const layerData = await layerResponse.json();
                const leafletLayer = L.geoJSON(layerData, {
                    style: function () { return referenceLayerStyle(layerInfo.id); },
                    onEachFeature: function (feature, featureLayer) {
                        const label = referenceLayerLabel(feature.properties);
                        if (!label) return;
                        const tooltip = document.createElement("span");
                        tooltip.textContent = label;
                        featureLayer.bindTooltip(tooltip, { sticky: true });
                    }
                });
                referenceLayerControl.addOverlay(leafletLayer, layerInfo.label);
                if (layerInfo.default_visible) leafletLayer.addTo(map);
            }));
        } catch (error) {
            // CAD call and unit mapping remains available if static references are absent.
        }
    }

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
    loadReferenceLayers();
})();
