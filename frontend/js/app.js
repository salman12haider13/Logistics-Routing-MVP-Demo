const API_BASE_URL = "http://localhost:5000/api";

const map = L.map("map").setView([30.3753, 69.3451], 6);

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "&copy; OpenStreetMap contributors"
}).addTo(map);

let startMarker = null;
let endMarker = null;
let routeLayers = [];

let startPoint = null;
let endPoint = null;
let blockedZones = [];
let trafficPoints = [];
let routesData = [];
let selectedRouteIndex = 0;
let selectionMode = "point";
let nextZoneId = 1;
let nextTrafficId = 1;

const blockedZoneLayerGroup = L.featureGroup().addTo(map);
const trafficLayerGroup = L.layerGroup().addTo(map);

const polygonDrawer = new L.Draw.Polygon(map, {
  showArea: true,
  allowIntersection: false,
  shapeOptions: {
    color: "#ff8a7a",
    fillColor: "#8b2d24",
    fillOpacity: 0.28,
    weight: 2
  }
});

const startCoordsEl = document.getElementById("start-coords");
const endCoordsEl = document.getElementById("end-coords");
const zoneCountEl = document.getElementById("zone-count");
const zoneListEl = document.getElementById("zone-list");
const trafficCountEl = document.getElementById("traffic-count");
const trafficListEl = document.getElementById("traffic-list");
const routeOptionsEl = document.getElementById("route-options");
const statusTextEl = document.getElementById("status-text");
const summaryRouteLabelEl = document.getElementById("summary-route-label");
const summaryModeEl = document.getElementById("summary-mode");
const summaryVehicleEl = document.getElementById("summary-vehicle");
const summaryDistanceEl = document.getElementById("summary-distance");
const summaryTimeEl = document.getElementById("summary-time");
const summaryStepsEl = document.getElementById("summary-steps");
const vehicleSelectEl = document.getElementById("vehicle-select");

const pointModeBtn = document.getElementById("point-mode-btn");
const drawZoneBtn = document.getElementById("draw-zone-btn");
const trafficModeBtn = document.getElementById("traffic-mode-btn");
const routeBtn = document.getElementById("route-btn");
const clearZonesBtn = document.getElementById("clear-zones-btn");
const clearTrafficBtn = document.getElementById("clear-traffic-btn");
const resetBtn = document.getElementById("reset-btn");

function formatCoords(lat, lon) {
  return `${lat.toFixed(6)}, ${lon.toFixed(6)}`;
}

function formatDistance(meters) {
  if (meters == null || Number.isNaN(meters)) return "-";
  if (meters >= 1000) return `${(meters / 1000).toFixed(2)} km`;
  return `${meters.toFixed(2)} m`;
}

function formatTime(seconds) {
  if (seconds == null || Number.isNaN(seconds)) return "-";

  const hrs = Math.floor(seconds / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  const secs = Math.round(seconds % 60);

  if (hrs > 0) return `${hrs}h ${mins}m ${secs}s`;
  if (mins > 0) return `${mins}m ${secs}s`;
  return `${secs}s`;
}

function getSelectedMode() {
  const checked = document.querySelector('input[name="mode"]:checked');
  return checked ? checked.value : "time";
}

function getSelectedVehicleLabel() {
  const option = vehicleSelectEl.options[vehicleSelectEl.selectedIndex];
  return option ? option.text : "Default";
}

function setStatus(message, type = "neutral") {
  statusTextEl.textContent = message;
  statusTextEl.className = "";

  if (type === "success") {
    statusTextEl.classList.add("status-success");
  } else if (type === "error") {
    statusTextEl.classList.add("status-error");
  } else {
    statusTextEl.classList.add("status-neutral");
  }
}

function setSelectionMode(mode) {
  selectionMode = mode;

  pointModeBtn.classList.toggle("active-mode-btn", mode === "point");
  trafficModeBtn.classList.toggle("active-mode-btn", mode === "traffic");

  if (mode === "point") {
    setStatus("Point mode active. Click map to set Point A and Point B.", "neutral");
  } else if (mode === "traffic") {
    setStatus("Traffic mode active. Click on the map to place traffic points.", "neutral");
  } else {
    setStatus("Blocked zone drawing active. Draw a polygon on the map.", "neutral");
  }
}

function clearRoutes() {
  routeLayers.forEach((layer) => {
    map.removeLayer(layer);
  });

  routeLayers = [];
  routesData = [];
  selectedRouteIndex = 0;

  routeOptionsEl.className = "route-options empty-list";
  routeOptionsEl.innerHTML = "No routes calculated";

  summaryRouteLabelEl.textContent = "-";
  summaryModeEl.textContent = "-";
  summaryVehicleEl.textContent = getSelectedVehicleLabel();
  summaryDistanceEl.textContent = "-";
  summaryTimeEl.textContent = "-";
  summaryStepsEl.textContent = "-";
}

function createTacticalDivIcon(className) {
  return L.divIcon({
    className: "",
    html: `<div class="${className}"></div>`,
    iconSize: [24, 34],
    iconAnchor: [12, 30],
    popupAnchor: [0, -24]
  });
}

function createTrafficVehicleIcon() {
  return L.divIcon({
    className: "",
    html: `
      <div class="traffic-vehicle-marker">
        <div class="traffic-vehicle-body"></div>
        <div class="traffic-vehicle-cabin"></div>
        <div class="traffic-vehicle-wheel-left"></div>
        <div class="traffic-vehicle-wheel-right"></div>
      </div>
    `,
    iconSize: [26, 16],
    iconAnchor: [13, 8],
    popupAnchor: [0, -8]
  });
}

function addStartMarker(latlng) {
  if (startMarker) map.removeLayer(startMarker);

  startMarker = L.marker(latlng, {
    title: "Point A",
    icon: createTacticalDivIcon("tactical-marker tactical-marker-start")
  }).addTo(map).bindPopup("Point A");
}

function addEndMarker(latlng) {
  if (endMarker) map.removeLayer(endMarker);

  endMarker = L.marker(latlng, {
    title: "Point B",
    icon: createTacticalDivIcon("tactical-marker tactical-marker-end")
  }).addTo(map).bindPopup("Point B");
}

function renderZoneList() {
  zoneCountEl.textContent = blockedZones.length;

  if (blockedZones.length === 0) {
    zoneListEl.className = "obstacle-list empty-list";
    zoneListEl.innerHTML = "No blocked zones added";
    return;
  }

  zoneListEl.className = "obstacle-list";
  zoneListEl.innerHTML = blockedZones.map((zone, index) => `
    <div class="obstacle-item">
      <div class="obstacle-text">
        <strong>Zone ${index + 1}</strong><br/>
        Vertices: ${zone.geometry.coordinates[0].length - 1}
      </div>
      <button class="obstacle-remove-btn" data-zone-id="${zone.id}" type="button">Remove</button>
    </div>
  `).join("");

  zoneListEl.querySelectorAll(".obstacle-remove-btn").forEach((btn) => {
    btn.addEventListener("click", () => removeBlockedZone(Number(btn.dataset.zoneId)));
  });
}

function renderTrafficList() {
  trafficCountEl.textContent = trafficPoints.length;

  if (trafficPoints.length === 0) {
    trafficListEl.className = "obstacle-list empty-list";
    trafficListEl.innerHTML = "No traffic points added";
    return;
  }

  trafficListEl.className = "obstacle-list";
  trafficListEl.innerHTML = trafficPoints.map((trafficPoint, index) => `
    <div class="obstacle-item">
      <div class="obstacle-text">
        <strong>Traffic ${index + 1}</strong><br/>
        ${formatCoords(trafficPoint.lat, trafficPoint.lon)}
      </div>
      <button class="obstacle-remove-btn" data-traffic-id="${trafficPoint.id}" type="button">Remove</button>
    </div>
  `).join("");

  trafficListEl.querySelectorAll(".obstacle-remove-btn").forEach((btn) => {
    btn.addEventListener("click", () => removeTrafficPoint(Number(btn.dataset.trafficId)));
  });
}

function addBlockedZoneFromLayer(layer) {
  const zoneId = nextZoneId++;
  const feature = layer.toGeoJSON();

  layer.setStyle({
    color: "#ff8a7a",
    fillColor: "#8b2d24",
    fillOpacity: 0.28,
    weight: 2
  });

  layer.bindPopup(`Blocked Zone ${zoneId}`);
  blockedZoneLayerGroup.addLayer(layer);

  blockedZones.push({
    id: zoneId,
    layer,
    geometry: feature.geometry
  });

  renderZoneList();
  clearRoutes();
  setSelectionMode("point");
  setStatus("Blocked zone added. Click Find Route to recalculate.", "neutral");
}

function removeBlockedZone(zoneId) {
  const zoneIndex = blockedZones.findIndex((zone) => zone.id === zoneId);
  if (zoneIndex === -1) return;

  blockedZoneLayerGroup.removeLayer(blockedZones[zoneIndex].layer);
  blockedZones.splice(zoneIndex, 1);

  renderZoneList();
  clearRoutes();
  setStatus("Blocked zone removed. Click Find Route to recalculate.", "neutral");
}

function clearBlockedZones() {
  blockedZones.forEach((zone) => blockedZoneLayerGroup.removeLayer(zone.layer));
  blockedZones = [];
  renderZoneList();
  clearRoutes();
}

function renderTrafficMarkers() {
  trafficLayerGroup.clearLayers();

  trafficPoints.forEach((trafficPoint, index) => {
    const marker = L.marker([trafficPoint.lat, trafficPoint.lon], {
      title: `Traffic ${index + 1}`,
      icon: createTrafficVehicleIcon()
    });

    marker.bindPopup(`
      <div>
        <strong>Traffic ${index + 1}</strong><br/>
        ${formatCoords(trafficPoint.lat, trafficPoint.lon)}
      </div>
    `);

    trafficLayerGroup.addLayer(marker);
  });
}

function addTrafficPoint(latlng) {
  trafficPoints.push({
    id: nextTrafficId++,
    lat: latlng.lat,
    lon: latlng.lng
  });

  renderTrafficMarkers();
  renderTrafficList();
  clearRoutes();
  setStatus("Traffic point added. Click Find Route to include traffic delays.", "neutral");
}

function removeTrafficPoint(trafficId) {
  const pointIndex = trafficPoints.findIndex((point) => point.id === trafficId);
  if (pointIndex === -1) return;

  trafficPoints.splice(pointIndex, 1);
  renderTrafficMarkers();
  renderTrafficList();
  clearRoutes();
  setStatus("Traffic point removed. Click Find Route to recalculate.", "neutral");
}

function clearTrafficPoints() {
  trafficPoints = [];
  renderTrafficMarkers();
  renderTrafficList();
  clearRoutes();
}

function resetAll() {
  startPoint = null;
  endPoint = null;

  if (startMarker) {
    map.removeLayer(startMarker);
    startMarker = null;
  }

  if (endMarker) {
    map.removeLayer(endMarker);
    endMarker = null;
  }

  clearBlockedZones();
  clearTrafficPoints();
  clearRoutes();

  startCoordsEl.textContent = "Not selected";
  endCoordsEl.textContent = "Not selected";
  summaryVehicleEl.textContent = getSelectedVehicleLabel();
  setSelectionMode("point");
  setStatus("Reset complete.", "neutral");
}

function handlePointPlacement(latlng) {
  const { lat, lng } = latlng;
  clearRoutes();

  if (!startPoint) {
    startPoint = { lat, lon: lng };
    addStartMarker(latlng);
    startCoordsEl.textContent = formatCoords(lat, lng);
    setStatus("Point A selected. Now click Point B.", "neutral");
    return;
  }

  if (!endPoint) {
    endPoint = { lat, lon: lng };
    addEndMarker(latlng);
    endCoordsEl.textContent = formatCoords(lat, lng);
    setStatus("Point B selected. You can draw zones, add traffic, or click Find Route.", "neutral");
    return;
  }

  if (startMarker) map.removeLayer(startMarker);
  if (endMarker) map.removeLayer(endMarker);

  startMarker = null;
  endMarker = null;

  startPoint = { lat, lon: lng };
  endPoint = null;

  addStartMarker(latlng);
  startCoordsEl.textContent = formatCoords(lat, lng);
  endCoordsEl.textContent = "Not selected";
  setStatus("Point selection restarted. Point A selected.", "neutral");
}

function speedClassToColor(speedClass) {
  if (speedClass === "fast") return "#1e90ff";
  if (speedClass === "medium") return "#ff9800";
  return "#ff3b30";
}

function routeFeatureStyle(feature, isSelected) {
  const speedClass = feature?.properties?.speed_class || "fast";

  return {
    color: speedClassToColor(speedClass),
    weight: isSelected ? 7 : 4,
    opacity: isSelected ? 0.96 : 0.45,
    lineCap: "round",
    lineJoin: "round"
  };
}

function renderRouteCards() {
  if (!routesData.length) {
    routeOptionsEl.className = "route-options empty-list";
    routeOptionsEl.innerHTML = "No routes calculated";
    return;
  }

  routeOptionsEl.className = "route-options";
  routeOptionsEl.innerHTML = routesData.map((route, index) => `
    <div class="route-option-card ${index === selectedRouteIndex ? "selected-route-card" : ""}" data-route-index="${index}">
      <div class="route-option-title">${route.label}${route.is_primary ? " (Primary)" : ""}</div>
      <div class="route-option-meta">
        Distance: ${formatDistance(route.summary.total_distance_m)}<br/>
        Time: ${formatTime(route.summary.total_time_sec)}<br/>
        Steps: ${route.summary.step_count}
      </div>
    </div>
  `).join("");

  routeOptionsEl.querySelectorAll(".route-option-card").forEach((card) => {
    card.addEventListener("click", () => {
      selectRoute(Number(card.dataset.routeIndex));
    });
  });
}

function drawRoutes() {
  routeLayers.forEach((layer) => map.removeLayer(layer));
  routeLayers = [];

  routesData.forEach((route, index) => {
    const isSelected = index === selectedRouteIndex;

    const layer = L.geoJSON(route.route_geojson, {
      style: (feature) => routeFeatureStyle(feature, isSelected)
    }).addTo(map);

    layer.on("click", () => {
      selectRoute(index);
    });

    routeLayers.push(layer);
  });

  routeLayers.forEach((layer, index) => {
    if (index === selectedRouteIndex && layer.bringToFront) {
      layer.bringToFront();
    }
  });
}

function updateSelectedRouteSummary() {
  if (!routesData.length || !routesData[selectedRouteIndex]) {
    summaryRouteLabelEl.textContent = "-";
    summaryModeEl.textContent = "-";
    summaryVehicleEl.textContent = getSelectedVehicleLabel();
    summaryDistanceEl.textContent = "-";
    summaryTimeEl.textContent = "-";
    summaryStepsEl.textContent = "-";
    return;
  }

  const route = routesData[selectedRouteIndex];
  summaryRouteLabelEl.textContent = route.label;
  summaryModeEl.textContent = getSelectedMode() === "time" ? "Minimum Time" : "Minimum Distance";
  summaryVehicleEl.textContent = getSelectedVehicleLabel();
  summaryDistanceEl.textContent = formatDistance(route.summary.total_distance_m);
  summaryTimeEl.textContent = formatTime(route.summary.total_time_sec);
  summaryStepsEl.textContent = route.summary.step_count;
}

function selectRoute(index) {
  if (index < 0 || index >= routesData.length) return;

  selectedRouteIndex = index;
  drawRoutes();
  renderRouteCards();
  updateSelectedRouteSummary();
  setStatus(`${routesData[index].label} selected.`, "success");
}

async function requestRoutes() {
  if (!startPoint || !endPoint) {
    setStatus("Please select both Point A and Point B.", "error");
    return;
  }

  const payload = {
    start: startPoint,
    end: endPoint,
    mode: getSelectedMode(),
    blocked_polygons: blockedZones.map((zone) => zone.geometry),
    traffic_points: trafficPoints.map((point) => ({
      lat: point.lat,
      lon: point.lon
    })),
    k: 3
  };

  setStatus("Finding routes...", "neutral");
  clearRoutes();

  try {
    const response = await fetch(`${API_BASE_URL}/route`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify(payload)
    });

    const result = await response.json();

    if (!response.ok) {
      throw new Error(result.error || "Routing request failed.");
    }

    routesData = result.routes || [];
    selectedRouteIndex = Number.isInteger(result.selected_route_index) ? result.selected_route_index : 0;

    if (!routesData.length) {
      throw new Error("No routes were returned.");
    }

    drawRoutes();
    renderRouteCards();
    updateSelectedRouteSummary();

    if (routeLayers[selectedRouteIndex]) {
      map.fitBounds(routeLayers[selectedRouteIndex].getBounds(), { padding: [30, 30] });
    }

    setStatus(`${routesData.length} route(s) found successfully.`, "success");
  } catch (error) {
    setStatus(error.message, "error");
    console.error("Routing error:", error);
  }
}

map.on("click", (e) => {
  if (selectionMode === "point") {
    handlePointPlacement(e.latlng);
  } else if (selectionMode === "traffic") {
    addTrafficPoint(e.latlng);
  }
});

map.on(L.Draw.Event.CREATED, (event) => {
  if (event.layerType === "polygon") {
    addBlockedZoneFromLayer(event.layer);
  }
});

pointModeBtn.addEventListener("click", () => {
  setSelectionMode("point");
});

drawZoneBtn.addEventListener("click", () => {
  setSelectionMode("zone");
  polygonDrawer.enable();
});

trafficModeBtn.addEventListener("click", () => {
  setSelectionMode("traffic");
});

clearZonesBtn.addEventListener("click", () => {
  clearBlockedZones();
  setStatus("All blocked zones cleared.", "neutral");
});

clearTrafficBtn.addEventListener("click", () => {
  clearTrafficPoints();
  setStatus("All traffic points cleared.", "neutral");
});

resetBtn.addEventListener("click", () => {
  resetAll();
});

vehicleSelectEl.addEventListener("change", () => {
  summaryVehicleEl.textContent = getSelectedVehicleLabel();
  clearRoutes();
  setStatus("Vehicle profile updated. Route rules can be added later.", "neutral");
});

routeBtn.addEventListener("click", requestRoutes);

renderZoneList();
renderTrafficList();
clearRoutes();
summaryVehicleEl.textContent = getSelectedVehicleLabel();
setSelectionMode("point");
setStatus("Point mode active. Click map to set Point A and Point B.", "neutral");