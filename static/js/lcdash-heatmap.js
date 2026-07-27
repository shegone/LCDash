(function () {
    "use strict";

    const dataElement = document.getElementById("heatmap-data");
    const mapElement = document.getElementById("activity-map");
    if (!dataElement || !mapElement) return;
    if (typeof L === "undefined") {
        mapElement.setAttribute("role", "alert");
        mapElement.textContent = "The map viewer could not be loaded. Refresh this page to try again.";
        return;
    }

    let heatmapData;
    try {
        heatmapData = JSON.parse(dataElement.textContent);
    } catch (error) {
        mapElement.setAttribute("role", "alert");
        mapElement.textContent = "Recent activity data could not be loaded.";
        return;
    }

    const defaultCenter = [37.8487, -81.9935];
    const map = L.map("activity-map", { zoomControl: true }).setView(defaultCenter, 10);

    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: "&copy; OpenStreetMap contributors"
    }).addTo(map);

    const agencyFilter = document.getElementById("heatmap-agency-filter");
    const visibleCalls = document.getElementById("heatmap-visible-calls");
    const visibleCells = document.getElementById("heatmap-visible-cells");
    const blendedButton = document.getElementById("heatmap-mode-blended");
    const individualButton = document.getElementById("heatmap-mode-individual");
    const legend = document.getElementById("heatmap-legend");
    const records = [];
    let displayMode = "blended";

    (heatmapData.features || []).forEach(function (feature) {
        if (!feature.geometry || feature.geometry.type !== "Point") return;
        const coordinates = feature.geometry.coordinates || [];
        const longitude = Number(coordinates[0]);
        const latitude = Number(coordinates[1]);
        if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return;

        records.push({
            latitude: latitude,
            longitude: longitude,
            properties: feature.properties || {}
        });
    });

    const heatLayer = typeof L.heatLayer === "function"
        ? L.heatLayer([], {
            radius: 38,
            blur: 30,
            maxZoom: 14,
            minOpacity: 0.36,
            gradient: {
                0.20: "#3578ff",
                0.40: "#28d7ff",
                0.65: "#ffd84d",
                0.82: "#ff8a3d",
                1.00: "#ff4c4c"
            }
        }).addTo(map)
        : null;
    const individualLayer = L.layerGroup().addTo(map);

    if (!heatLayer) {
        displayMode = "individual";
        if (blendedButton) {
            blendedButton.disabled = true;
            blendedButton.title = "Blended heat view is temporarily unavailable";
        }
    }

    function selectedCount(record) {
        const selectedAgency = agencyFilter ? agencyFilter.value : "";
        if (!selectedAgency) return Number(record.properties.count || 0);
        const agencyCounts = record.properties.agency_counts || {};
        return Number(agencyCounts[selectedAgency] || 0);
    }

    function selectedAgencyCounts(record) {
        const selectedAgency = agencyFilter ? agencyFilter.value : "";
        const agencyCounts = record.properties.agency_counts || {};
        if (selectedAgency) {
            const selected = Number(agencyCounts[selectedAgency] || 0);
            return selected > 0 ? [{ agency: selectedAgency, count: selected }] : [];
        }

        const result = Object.keys(agencyCounts).map(function (agency) {
            return { agency: agency, count: Number(agencyCounts[agency] || 0) };
        }).filter(function (item) { return item.count > 0; });

        const knownCount = result.reduce(function (total, item) { return total + item.count; }, 0);
        const unknownCount = Math.max(0, Number(record.properties.count || 0) - knownCount);
        if (unknownCount > 0) result.push({ agency: "Unknown", count: unknownCount });
        return result;
    }

    function individualPoint(latitude, longitude, index, total) {
        if (total <= 1) return [latitude, longitude];
        const angle = (Math.PI * 2 * index) / total;
        const ring = 1 + Math.floor(index / 10);
        const distance = 0.00075 * ring;
        return [
            latitude + Math.sin(angle) * distance,
            longitude + Math.cos(angle) * distance
        ];
    }

    function createIndividualMarker(latitude, longitude, agency) {
        const marker = L.circleMarker([latitude, longitude], {
            radius: 6,
            color: "#ffffff",
            weight: 1.5,
            fillColor: "#27d8ff",
            fillOpacity: 0.88
        });

        const popup = document.createElement("div");
        const title = document.createElement("strong");
        title.textContent = "Historical call";
        popup.appendChild(title);
        const detail = document.createElement("div");
        detail.textContent = agency && agency !== "Unknown"
            ? agency + " • Approximate location"
            : "Approximate location";
        popup.appendChild(detail);
        marker.bindPopup(popup);
        return marker;
    }

    function setDisplayButtons() {
        const blended = displayMode === "blended";
        if (blendedButton) {
            blendedButton.classList.toggle("active", blended);
            blendedButton.setAttribute("aria-pressed", String(blended));
        }
        if (individualButton) {
            individualButton.classList.toggle("active", !blended);
            individualButton.setAttribute("aria-pressed", String(!blended));
        }
        if (legend) legend.style.display = blended ? "flex" : "none";
    }

    function applyView(fitMap) {
        const visibleRecords = records
            .map(function (record) {
                return { record: record, count: selectedCount(record) };
            })
            .filter(function (item) { return item.count > 0; });

        const maximumCount = Math.max.apply(
            null,
            visibleRecords.map(function (item) { return item.count; }).concat([1])
        );
        let callCount = 0;
        const heatPoints = [];
        const boundsPoints = [];
        individualLayer.clearLayers();

        visibleRecords.forEach(function (item) {
            const record = item.record;
            const intensity = 0.22 + (0.78 * item.count / maximumCount);
            heatPoints.push([record.latitude, record.longitude, intensity]);
            callCount += item.count;

            if (displayMode === "individual") {
                let pointIndex = 0;
                selectedAgencyCounts(record).forEach(function (agencyItem) {
                    for (let index = 0; index < agencyItem.count; index += 1) {
                        const point = individualPoint(
                            record.latitude,
                            record.longitude,
                            pointIndex,
                            item.count
                        );
                        createIndividualMarker(point[0], point[1], agencyItem.agency).addTo(individualLayer);
                        boundsPoints.push(point);
                        pointIndex += 1;
                    }
                });
            } else {
                boundsPoints.push([record.latitude, record.longitude]);
            }
        });

        if (heatLayer) heatLayer.setLatLngs(displayMode === "blended" ? heatPoints : []);
        if (visibleCalls) visibleCalls.textContent = String(callCount);
        if (visibleCells) visibleCells.textContent = String(visibleRecords.length);

        setDisplayButtons();

        if (fitMap && boundsPoints.length) {
            const bounds = L.latLngBounds(boundsPoints);
            if (bounds.isValid()) map.fitBounds(bounds.pad(0.18), { maxZoom: 13 });
        }
    }

    function setDisplayMode(mode) {
        displayMode = mode === "individual" || !heatLayer ? "individual" : "blended";
        applyView(true);
    }

    if (agencyFilter) {
        agencyFilter.addEventListener("change", function () { applyView(true); });
    }
    if (blendedButton) {
        blendedButton.addEventListener("click", function () { setDisplayMode("blended"); });
    }
    if (individualButton) {
        individualButton.addEventListener("click", function () { setDisplayMode("individual"); });
    }

    const fitButton = document.getElementById("fit-heatmap-activity");
    if (fitButton) fitButton.addEventListener("click", function () { applyView(true); });

    const clearButton = document.getElementById("clear-heatmap-filters");
    if (clearButton) {
        clearButton.addEventListener("click", function () {
            if (agencyFilter) agencyFilter.value = "";
            applyView(true);
        });
    }

    applyView(true);
})();
