from __future__ import annotations

import json
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.data_loader.area_loader import AREA_DATASETS, MAX_RADIUS_KM, load_area_datasets
from app.database import get_db

logger = logging.getLogger(__name__)

DatasetKey = Literal[
    "substations",
    "hospitals",
    "cell_towers",
    "fire_stations",
    "ems_stations",
    "water_treatment",
    "nine11_centers",
    "psap",
    "911_centers",
    "shelters",
    "police_stations",
]

router = APIRouter(prefix="/api/v1", tags=["loader"])


class AreaCenter(BaseModel):
    latitude: float
    longitude: float


class AreaLoadRequest(BaseModel):
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    radius_km: float = Field(..., gt=0, le=MAX_RADIUS_KM)
    datasets: list[DatasetKey] | None = None


class AreaLoadResponse(BaseModel):
    center: AreaCenter
    radius_km: float
    assets_loaded: dict[str, int]
    total: int


@router.post("/loader/area", response_model=AreaLoadResponse)
async def load_area(
    body: AreaLoadRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    try:
        assets_loaded = await load_area_datasets(
            session=db,
            latitude=body.latitude,
            longitude=body.longitude,
            radius_km=body.radius_km,
            dataset_keys=list(body.datasets) if body.datasets else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    graph_service = request.app.state.graph_service
    await graph_service.rebuild(db)
    logger.info(
        "Graph rebuilt after area load: %d nodes, %d edges",
        graph_service.graph.number_of_nodes(),
        graph_service.graph.number_of_edges(),
    )

    return AreaLoadResponse(
        center=AreaCenter(latitude=body.latitude, longitude=body.longitude),
        radius_km=body.radius_km,
        assets_loaded=assets_loaded,
        total=sum(assets_loaded.values()),
    )


@router.get("/map", response_class=HTMLResponse, include_in_schema=False)
async def map_page():
    html = _MAP_HTML.replace("__MAPBOX_TOKEN__", json.dumps(settings.MAPBOX_TOKEN))
    html = html.replace("__DATASET_KEYS__", json.dumps(sorted(AREA_DATASETS)))
    return HTMLResponse(content=html)


_MAP_HTML = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TriNetra Infrastructure Map</title>
  <link href="https://api.mapbox.com/mapbox-gl-js/v3.4.0/mapbox-gl.css" rel="stylesheet">
  <script src="https://api.mapbox.com/mapbox-gl-js/v3.4.0/mapbox-gl.js"></script>
  <style>
    :root {
      color-scheme: dark;
      --ink: oklch(94% 0.012 255);
      --muted: oklch(73% 0.025 255);
      --surface: oklch(18% 0.025 255);
      --panel: oklch(24% 0.025 255 / 0.9);
      --line: oklch(43% 0.035 255);
      --shadow: 0 22px 55px oklch(5% 0.02 255 / 0.44);
    }

    * {
      box-sizing: border-box;
    }

    body {
      margin: 0;
      color: var(--ink);
      background: var(--surface);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }

    #map {
      position: fixed;
      inset: 0;
    }

    .panel,
    .legend,
    .notice {
      position: absolute;
      z-index: 2;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 16px;
      box-shadow: var(--shadow);
      color: var(--ink);
    }

    .panel {
      top: 18px;
      left: 18px;
      width: min(310px, calc(100vw - 36px));
      padding: 18px;
    }

    .panel h1 {
      margin: 0 0 4px;
      font-size: 18px;
      line-height: 1.2;
      letter-spacing: -0.02em;
    }

    .summary {
      margin: 0 0 15px;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.45;
    }

    .filter-list {
      display: grid;
      gap: 9px;
    }

    .filter-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      font-size: 13px;
    }

    .filter-row label {
      display: inline-flex;
      align-items: center;
      gap: 9px;
      min-width: 0;
      cursor: pointer;
    }

    .filter-row input {
      accent-color: oklch(62% 0.18 252);
    }

    .count {
      color: var(--muted);
      font-variant-numeric: tabular-nums;
    }

    .control-title {
      margin: 12px 0 2px;
      color: var(--muted);
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }

    .control-title:first-child {
      margin-top: 0;
    }

    .swatch {
      width: 10px;
      height: 10px;
      flex: 0 0 auto;
      border-radius: 999px;
      box-shadow: 0 0 0 1px oklch(20% 0.02 255 / 0.18);
    }

    .legend {
      right: 18px;
      bottom: 18px;
      width: min(240px, calc(100vw - 36px));
      padding: 14px;
    }

    .legend-title {
      margin: 0 0 10px;
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--muted);
    }

    .legend-list {
      display: grid;
      gap: 8px;
      font-size: 13px;
    }

    .legend-row {
      display: flex;
      align-items: center;
      gap: 9px;
    }

    .notice {
      left: 50%;
      top: 50%;
      width: min(420px, calc(100vw - 36px));
      padding: 22px;
      transform: translate(-50%, -50%);
    }

    .notice h2 {
      margin: 0 0 8px;
      font-size: 18px;
    }

    .notice p {
      margin: 0;
      color: var(--muted);
      line-height: 1.5;
    }

    .mapboxgl-popup-content {
      background: oklch(24% 0.025 255);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 12px 14px;
      color: var(--ink);
      box-shadow: var(--shadow);
    }

    .mapboxgl-popup-tip {
      border-top-color: oklch(24% 0.025 255) !important;
      border-bottom-color: oklch(24% 0.025 255) !important;
    }

    .popup-title {
      margin: 0 0 5px;
      font-size: 14px;
      font-weight: 700;
    }

    .popup-meta {
      margin: 0;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
    }

    .popup-section {
      margin: 12px 0 0;
      font-size: 12px;
      line-height: 1.45;
    }

    .popup-section-title {
      margin: 0 0 5px;
      color: var(--ink);
      font-weight: 700;
    }

    .popup-list,
    .popup-empty {
      margin: 0;
      color: var(--muted);
    }

    .popup-list {
      padding-left: 16px;
    }

    @media (max-width: 720px) {
      .legend {
        display: none;
      }

      .panel {
        top: 12px;
        left: 12px;
        width: calc(100vw - 24px);
        max-height: 42vh;
        overflow: auto;
      }
    }
  </style>
</head>
<body>
  <div id="map"></div>
  <section class="panel" aria-label="Map controls">
    <h1>Infrastructure Assets</h1>
    <p class="summary"><span id="asset-total">0</span> assets loaded from the API</p>
    <div id="filters" class="filter-list"></div>
  </section>
  <aside class="legend" aria-label="Asset type legend">
    <p class="legend-title">Asset Types</p>
    <div id="legend" class="legend-list"></div>
  </aside>

  <script>
    const MAPBOX_TOKEN = __MAPBOX_TOKEN__;
    const DATASET_KEYS = __DATASET_KEYS__;
    const TYPE_COLORS = {
      substation: "#F59E0B",
      hospital: "#FF6B6B",
      water_treatment: "#06B6D4",
      cell_tower: "#3B82F6",
      fire_station: "#F97316",
      ems_station: "#10B981",
      "911_center": "#FF0000",
      shelter: "#8B5CF6",
      police_station: "#6B7280",
      default: "#6B7280"
    };

    const TYPE_LABELS = {
      substation: "Substations",
      hospital: "Hospitals",
      water_treatment: "Water Treatment",
      cell_tower: "Cell Towers",
      fire_station: "Fire Stations",
      ems_station: "EMS Stations",
      "911_center": "911 Centers",
      shelter: "Shelters",
      police_station: "Police Stations"
    };

    const DEPENDENCY_TYPES = ["power", "water", "communications"];
    const DEPENDENCY_STYLES = {
      power: { layerId: "edges-power", label: "Power Lines", color: "#F59E0B" },
      water: { layerId: "edges-water", label: "Water Lines", color: "#06B6D4" },
      communications: { layerId: "edges-comms", label: "Comms Lines", color: "#3B82F6" }
    };

    const DEPENDENCY_LABELS = {
      power: "Power",
      water: "Water",
      communications: "Comms"
    };

    const ORDERED_TYPES = [
      "substation",
      "hospital",
      "water_treatment",
      "cell_tower",
      "911_center",
      "fire_station",
      "ems_station",
      "shelter",
      "police_station"
    ];

    function showNotice(title, message) {
      const notice = document.createElement("section");
      notice.className = "notice";
      notice.innerHTML = `<h2>${escapeHtml(title)}</h2><p>${escapeHtml(message)}</p>`;
      document.body.appendChild(notice);
    }

    function escapeHtml(value) {
      return String(value ?? "").replace(/[&<>"']/g, (char) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        "\"": "&quot;",
        "'": "&#039;"
      })[char]);
    }

    function colorFor(type) {
      return TYPE_COLORS[type] || TYPE_COLORS.default;
    }

    function labelFor(type) {
      return TYPE_LABELS[type] || type.replaceAll("_", " ");
    }

    function canonicalType(type) {
      return type === "nine11_center" ? "911_center" : type;
    }

    async function fetchAllAssets() {
      const limit = 1000;
      let offset = 0;
      let total = null;
      const features = [];

      do {
        const response = await fetch(`/api/v1/assets?limit=${limit}&offset=${offset}`);
        if (!response.ok) {
          throw new Error(`Asset request failed with status ${response.status}`);
        }
        const collection = await response.json();
        total = collection.total_count || 0;
        features.push(...(collection.features || []).map((feature) => ({
          ...feature,
          properties: {
            ...(feature.properties || {}),
            asset_type: canonicalType(feature.properties?.asset_type || "default")
          }
        })));
        offset += limit;
      } while (features.length < total);

      return {
        type: "FeatureCollection",
        features,
        total_count: total ?? features.length
      };
    }

    async function fetchDependenciesGeo() {
      const response = await fetch("/api/v1/dependencies/geo");
      if (!response.ok) {
        throw new Error(`Dependency request failed with status ${response.status}`);
      }
      const collection = await response.json();
      return {
        type: "FeatureCollection",
        features: (collection.features || []).map((feature) => ({
          ...feature,
          id: feature.id || feature.properties?.id,
          properties: {
            ...(feature.properties || {}),
            upstream_type: canonicalType(feature.properties?.upstream_type || "default"),
            downstream_type: canonicalType(feature.properties?.downstream_type || "default")
          }
        }))
      };
    }

    function buildTypeStats(features) {
      const stats = new Map();
      for (const type of ORDERED_TYPES) {
        stats.set(type, 0);
      }
      for (const feature of features) {
        const type = canonicalType(feature.properties?.asset_type || "default");
        stats.set(type, (stats.get(type) || 0) + 1);
      }
      const known = ORDERED_TYPES.map((type) => [type, stats.get(type) || 0]);
      const unknown = [...stats.entries()]
        .filter(([type]) => !ORDERED_TYPES.includes(type))
        .sort(([a], [b]) => labelFor(a).localeCompare(labelFor(b)));
      return [...known, ...unknown];
    }

    function buildDependencyStats(features) {
      const stats = new Map(DEPENDENCY_TYPES.map((type) => [type, 0]));
      for (const feature of features) {
        const type = feature.properties?.dependency_type;
        if (type) stats.set(type, (stats.get(type) || 0) + 1);
      }
      return DEPENDENCY_TYPES.map((type) => [type, stats.get(type) || 0]);
    }

    function buildDependencyLookups(features) {
      const upstreamOf = {};
      const downstreamOf = {};

      for (const feature of features) {
        const props = feature.properties || {};
        if (!props.upstream_asset_id || !props.downstream_asset_id) continue;

        if (!upstreamOf[props.downstream_asset_id]) upstreamOf[props.downstream_asset_id] = [];
        upstreamOf[props.downstream_asset_id].push({
          name: props.upstream_name,
          type: props.upstream_type,
          dep_type: props.dependency_type,
          upstream_id: props.upstream_asset_id
        });

        if (!downstreamOf[props.upstream_asset_id]) downstreamOf[props.upstream_asset_id] = [];
        downstreamOf[props.upstream_asset_id].push({
          name: props.downstream_name,
          type: props.downstream_type,
          dep_type: props.dependency_type,
          downstream_id: props.downstream_asset_id
        });
      }

      return { upstreamOf, downstreamOf };
    }

    function renderControls(
      typeStats,
      dependencyStats,
      visibleTypes,
      visibleDependencyTypes,
      onAssetChange,
      onDependencyChange
    ) {
      const filters = document.getElementById("filters");
      const legend = document.getElementById("legend");
      filters.innerHTML = "";
      legend.innerHTML = "";

      const assetsTitle = document.createElement("p");
      assetsTitle.className = "control-title";
      assetsTitle.textContent = "Assets";
      filters.appendChild(assetsTitle);

      for (const [type, count] of typeStats) {
        const filterRow = document.createElement("div");
        filterRow.className = "filter-row";
        filterRow.innerHTML = `
          <label>
            <input type="checkbox" value="${escapeHtml(type)}" checked>
            <span class="swatch" style="background:${colorFor(type)}"></span>
            <span>${escapeHtml(labelFor(type))}</span>
          </label>
          <span class="count">${count}</span>
        `;
        filterRow.querySelector("input").addEventListener("change", (event) => {
          if (event.target.checked) {
            visibleTypes.add(type);
          } else {
            visibleTypes.delete(type);
          }
          onAssetChange();
        });
        filters.appendChild(filterRow);

        const legendRow = document.createElement("div");
        legendRow.className = "legend-row";
        legendRow.innerHTML = `
          <span class="swatch" style="background:${colorFor(type)}"></span>
          <span>${escapeHtml(labelFor(type))}</span>
        `;
        legend.appendChild(legendRow);
      }

      const edgesTitle = document.createElement("p");
      edgesTitle.className = "control-title";
      edgesTitle.textContent = "Dependency Lines";
      filters.appendChild(edgesTitle);

      for (const [type, count] of dependencyStats) {
        const style = DEPENDENCY_STYLES[type];
        const edgeRow = document.createElement("div");
        edgeRow.className = "filter-row";
        edgeRow.innerHTML = `
          <label>
            <input type="checkbox" value="${escapeHtml(type)}" checked>
            <span class="swatch" style="background:${style.color}"></span>
            <span>${escapeHtml(style.label)}</span>
          </label>
          <span class="count">${count}</span>
        `;
        edgeRow.querySelector("input").addEventListener("change", (event) => {
          if (event.target.checked) {
            visibleDependencyTypes.add(type);
          } else {
            visibleDependencyTypes.delete(type);
          }
          onDependencyChange();
        });
        filters.appendChild(edgeRow);
      }
    }

    function fitToFeatures(map, features) {
      if (!features.length) {
        map.setCenter([-66.5, 18.2]);
        map.setZoom(7);
        return;
      }

      const bounds = new mapboxgl.LngLatBounds();
      for (const feature of features) {
        const coords = feature.geometry?.coordinates;
        if (Array.isArray(coords) && coords.length >= 2) {
          bounds.extend(coords);
        }
      }

      if (!bounds.isEmpty()) {
        map.fitBounds(bounds, { padding: 70, maxZoom: 12, duration: 700 });
      }
    }

    function dependencyLabel(type) {
      return DEPENDENCY_LABELS[type] || type.replaceAll("_", " ");
    }

    function summarizeTypeCounts(items) {
      const counts = new Map();
      for (const item of items) {
        counts.set(item.type, (counts.get(item.type) || 0) + 1);
      }
      return [...counts.entries()]
        .sort(([a], [b]) => labelFor(a).localeCompare(labelFor(b)))
        .map(([type, count]) => `${count} ${labelFor(type).toLowerCase()}`)
        .join(", ");
    }

    function groupedDependencyList(items, summarizeLargeGroups = false) {
      if (!items.length) {
        return `<p class="popup-empty">(none)</p>`;
      }

      const byDependencyType = new Map();
      for (const item of items) {
        const type = item.dep_type || "unknown";
        if (!byDependencyType.has(type)) byDependencyType.set(type, []);
        byDependencyType.get(type).push(item);
      }

      const rows = [...byDependencyType.entries()].map(([type, group]) => {
        const content = summarizeLargeGroups && group.length > 5
          ? summarizeTypeCounts(group)
          : group
              .slice(0, 5)
              .map((item) => `${escapeHtml(item.name || "Unnamed")} (${escapeHtml(labelFor(item.type))})`)
              .join(", ") + (group.length > 5 ? `, +${group.length - 5} more` : "");
        return `<li><strong>${escapeHtml(dependencyLabel(type))}:</strong> ${content}</li>`;
      });

      return `<ul class="popup-list">${rows.join("")}</ul>`;
    }

    function popupHtml(feature, dependencyLookups) {
      const props = feature.properties || {};
      const coords = feature.geometry?.coordinates || [];
      const lon = Number(coords[0]);
      const lat = Number(coords[1]);
      const coordText = Number.isFinite(lat) && Number.isFinite(lon)
        ? `${lat.toFixed(5)}, ${lon.toFixed(5)}`
        : "Unknown coordinates";
      const assetId = String(props.id || feature.id || "");
      const upstream = dependencyLookups.upstreamOf[assetId] || [];
      const downstream = dependencyLookups.downstreamOf[assetId] || [];

      return `
        <p class="popup-title">${escapeHtml(props.name || "Unnamed asset")}</p>
        <p class="popup-meta">
          Type: ${escapeHtml(labelFor(props.asset_type || "default"))}<br>
          Criticality tier: ${escapeHtml(props.criticality_tier || "unknown")}<br>
          Coordinates: ${escapeHtml(coordText)}
        </p>
        <div class="popup-section">
          <p class="popup-section-title">Depends on</p>
          ${groupedDependencyList(upstream)}
        </div>
        <div class="popup-section">
          <p class="popup-section-title">Provides to</p>
          ${groupedDependencyList(downstream, true)}
        </div>
      `;
    }

    function dependencyLayerFilter(type) {
      return ["==", ["get", "dependency_type"], type];
    }

    function setDependencyVisibility(map, visibleDependencyTypes) {
      for (const type of DEPENDENCY_TYPES) {
        const layerId = DEPENDENCY_STYLES[type].layerId;
        if (map.getLayer(layerId)) {
          map.setLayoutProperty(
            layerId,
            "visibility",
            visibleDependencyTypes.has(type) ? "visible" : "none"
          );
        }
      }
    }

    function setDependencyHighlight(map, assetId) {
      for (const type of DEPENDENCY_TYPES) {
        const layerId = DEPENDENCY_STYLES[type].layerId;
        if (!map.getLayer(layerId)) continue;

        if (!assetId) {
          map.setPaintProperty(layerId, "line-opacity", 0.15);
          map.setPaintProperty(layerId, "line-width", 1);
          continue;
        }

        const connected = [
          "any",
          ["==", ["get", "upstream_asset_id"], assetId],
          ["==", ["get", "downstream_asset_id"], assetId]
        ];
        map.setPaintProperty(layerId, "line-opacity", ["case", connected, 0.7, 0.05]);
        map.setPaintProperty(layerId, "line-width", ["case", connected, 2, 1]);
      }
    }

    async function boot() {
      if (!MAPBOX_TOKEN) {
        showNotice("Mapbox token missing", "Set MAPBOX_TOKEN in your .env file, then restart the FastAPI server.");
        return;
      }

      mapboxgl.accessToken = MAPBOX_TOKEN;
      const map = new mapboxgl.Map({
        container: "map",
        style: "mapbox://styles/mapbox/dark-v11",
        center: [-66.5, 18.2],
        zoom: 7,
        pitch: 68,
        bearing: -15,
        antialias: true,
        attributionControl: true
      });
      map.addControl(new mapboxgl.NavigationControl({ visualizePitch: true }), "top-right");

      const collection = await fetchAllAssets();
      const features = collection.features || [];
      const dependencies = await fetchDependenciesGeo();
      const dependencyFeatures = dependencies.features || [];
      const dependencyLookups = buildDependencyLookups(dependencyFeatures);
      document.getElementById("asset-total").textContent = String(collection.total_count ?? features.length);

      const typeStats = buildTypeStats(features);
      const dependencyStats = buildDependencyStats(dependencyFeatures);
      const visibleTypes = new Set(typeStats.map(([type]) => type));
      const visibleDependencyTypes = new Set(DEPENDENCY_TYPES);
      renderControls(
        typeStats,
        dependencyStats,
        visibleTypes,
        visibleDependencyTypes,
        () => {
          const filter = ["in", ["get", "asset_type"], ["literal", [...visibleTypes]]];
          if (map.getLayer("assets-glow")) map.setFilter("assets-glow", filter);
          if (map.getLayer("assets-points")) map.setFilter("assets-points", filter);
        },
        () => setDependencyVisibility(map, visibleDependencyTypes)
      );

      map.on("load", () => {
        map.addSource("mapbox-dem", {
          type: "raster-dem",
          url: "mapbox://mapbox.mapbox-terrain-dem-v1",
          tileSize: 512,
          maxzoom: 14
        });
        map.setTerrain({ source: "mapbox-dem", exaggeration: 1.35 });
        map.setFog({
          color: "rgb(18, 24, 38)",
          "high-color": "rgb(36, 60, 96)",
          "horizon-blend": 0.18,
          "space-color": "rgb(8, 11, 19)",
          "star-intensity": 0.18
        });

        const labelLayer = map.getStyle().layers.find((layer) => {
          return layer.type === "symbol" && layer.layout && layer.layout["text-field"];
        });
        if (map.getSource("composite")) {
          map.addLayer({
            id: "3d-buildings",
            source: "composite",
            "source-layer": "building",
            filter: ["==", ["get", "extrude"], "true"],
            type: "fill-extrusion",
            minzoom: 14,
            paint: {
              "fill-extrusion-color": "#465165",
              "fill-extrusion-height": [
                "interpolate",
                ["linear"],
                ["zoom"],
                14, 0,
                15.5, ["get", "height"]
              ],
              "fill-extrusion-base": [
                "interpolate",
                ["linear"],
                ["zoom"],
                14, 0,
                15.5, ["get", "min_height"]
              ],
              "fill-extrusion-opacity": 0.64
            }
          }, labelLayer?.id);
        }

        map.addSource("dependencies-source", {
          type: "geojson",
          data: dependencies
        });

        for (const type of DEPENDENCY_TYPES) {
          const style = DEPENDENCY_STYLES[type];
          map.addLayer({
            id: style.layerId,
            type: "line",
            source: "dependencies-source",
            filter: dependencyLayerFilter(type),
            layout: {
              "line-cap": "round",
              "line-join": "round"
            },
            paint: {
              "line-color": style.color,
              "line-width": 1,
              "line-opacity": 0.15,
              "line-emissive-strength": 1
            }
          });
        }
        setDependencyVisibility(map, visibleDependencyTypes);

        map.addSource("assets-source", {
          type: "geojson",
          data: { type: "FeatureCollection", features }
        });

        map.addLayer({
          id: "assets-glow",
          type: "circle",
          source: "assets-source",
          paint: {
            "circle-color": [
              "match",
              ["get", "asset_type"],
              "substation", TYPE_COLORS.substation,
              "hospital", TYPE_COLORS.hospital,
              "water_treatment", TYPE_COLORS.water_treatment,
              "cell_tower", TYPE_COLORS.cell_tower,
              "fire_station", TYPE_COLORS.fire_station,
              "ems_station", TYPE_COLORS.ems_station,
              "911_center", TYPE_COLORS["911_center"],
              "shelter", TYPE_COLORS.shelter,
              "police_station", TYPE_COLORS.police_station,
              TYPE_COLORS.default
            ],
            "circle-radius": [
              "match",
              ["get", "criticality_tier"],
              1, 16,
              2, 13,
              3, 10,
              4, 8,
              9
            ],
            "circle-blur": 1.25,
            "circle-opacity": 0.52,
            "circle-emissive-strength": 1
          }
        });

        map.addLayer({
          id: "assets-points",
          type: "circle",
          source: "assets-source",
          paint: {
            "circle-color": [
              "match",
              ["get", "asset_type"],
              "substation", TYPE_COLORS.substation,
              "hospital", TYPE_COLORS.hospital,
              "water_treatment", TYPE_COLORS.water_treatment,
              "cell_tower", TYPE_COLORS.cell_tower,
              "fire_station", TYPE_COLORS.fire_station,
              "ems_station", TYPE_COLORS.ems_station,
              "911_center", TYPE_COLORS["911_center"],
              "shelter", TYPE_COLORS.shelter,
              "police_station", TYPE_COLORS.police_station,
              TYPE_COLORS.default
            ],
            "circle-radius": [
              "match",
              ["get", "criticality_tier"],
              1, 3,
              2, 2.4,
              3, 2,
              4, 1.7,
              2
            ],
            "circle-stroke-color": "rgba(248, 250, 252, 0.72)",
            "circle-stroke-width": 0.7,
            "circle-opacity": 0.98,
            "circle-emissive-strength": 1
          }
        });

        map.on("click", "assets-points", (event) => {
          const feature = event.features?.[0];
          if (!feature) return;
          new mapboxgl.Popup({ closeButton: true })
            .setLngLat(feature.geometry.coordinates)
            .setHTML(popupHtml(feature, dependencyLookups))
            .addTo(map);
        });

        map.on("mouseenter", "assets-points", () => {
          map.getCanvas().style.cursor = "pointer";
        });

        let hoveredAssetId = null;
        map.on("mousemove", "assets-points", (event) => {
          const feature = event.features?.[0];
          const assetId = String(feature?.properties?.id || feature?.id || "");
          if (!assetId || assetId === hoveredAssetId) return;
          hoveredAssetId = assetId;
          setDependencyHighlight(map, assetId);
        });

        map.on("mouseleave", "assets-points", () => {
          map.getCanvas().style.cursor = "";
          hoveredAssetId = null;
          setDependencyHighlight(map, null);
        });

        fitToFeatures(map, features);
      });
    }

    boot().catch((error) => {
      console.error(error);
      showNotice("Unable to load map data", error.message || "Check the FastAPI server logs for details.");
    });
  </script>
</body>
</html>
"""
