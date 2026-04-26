from __future__ import annotations

import json
from typing import Any

DAMAGE_COLORS = {
    "destroyed": "#EF4444",
    "major-damage": "#F97316",
    "minor-damage": "#EAB308",
    "no-damage": "#22C55E",
    "skipped": "#6B7280",
}

DAMAGE_LABELS = {
    "destroyed": "Destroyed",
    "major-damage": "Major Damage",
    "minor-damage": "Minor Damage",
    "no-damage": "No Damage",
    "skipped": "Skipped",
}

EDGE_COLORS = {
    "power": "#F59E0B",
    "water": "#06B6D4",
    "communications": "#3B82F6",
}


def _json_for_script(value: object) -> str:
    return json.dumps(value).replace("</", "<\\/")


def _asset_features(result: Any) -> list[dict[str, Any]]:
    observations_by_asset = {
        observation["asset_id"]: observation
        for observation in result.damage_observations
    }

    features = []
    for asset in result.assets:
        observation = observations_by_asset.get(asset["asset_id"])
        damage_level = observation["damage_level"] if observation else "skipped"
        probabilities = observation.get("raw", {}).get("class_probabilities", {}) if observation else {}
        raw = observation.get("raw", {}) if observation else {}
        features.append(
            {
                "type": "Feature",
                "id": asset["asset_id"],
                "geometry": {
                    "type": "Point",
                    "coordinates": [asset["longitude"], asset["latitude"]],
                },
                "properties": {
                    "id": asset["asset_id"],
                    "asset_id": asset["asset_id"],
                    "name": asset.get("name") or "Unnamed asset",
                    "asset_type": asset["asset_type"],
                    "criticality_tier": asset.get("criticality_tier"),
                    "damage_level": damage_level,
                    "damage_label": DAMAGE_LABELS[damage_level],
                    "damage_color": DAMAGE_COLORS[damage_level],
                    "confidence": observation.get("confidence") if observation else None,
                    "raw": raw,
                    "class_probabilities": probabilities,
                    "observation_id": observation.get("observation_id") if observation else None,
                    "source_detail": observation.get("source_detail") if observation else None,
                    "timestamp": observation.get("timestamp") if observation else None,
                },
            }
        )
    return features


def _edge_features(result: Any, assets_by_id: dict[str, dict[str, Any]], damage_by_id: dict[str, str]) -> list[dict[str, Any]]:
    features = []
    for index, edge in enumerate(result.edges):
        upstream = assets_by_id.get(edge["upstream_id"])
        downstream = assets_by_id.get(edge["downstream_id"])
        if upstream is None or downstream is None:
            continue
        features.append(
            {
                "type": "Feature",
                "id": f"edge-{index}",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [upstream["longitude"], upstream["latitude"]],
                        [downstream["longitude"], downstream["latitude"]],
                    ],
                },
                "properties": {
                    "edge_key": f"{edge['upstream_id']}|{edge['downstream_id']}|{edge['dependency_type']}",
                    "upstream_asset_id": edge["upstream_id"],
                    "downstream_asset_id": edge["downstream_id"],
                    "dependency_type": edge["dependency_type"],
                    "criticality": edge["criticality"],
                    "upstream_name": upstream.get("name") or "Unnamed asset",
                    "downstream_name": downstream.get("name") or "Unnamed asset",
                    "upstream_type": upstream["asset_type"],
                    "downstream_type": downstream["asset_type"],
                    "upstream_damage": damage_by_id.get(edge["upstream_id"], "skipped"),
                    "downstream_damage": damage_by_id.get(edge["downstream_id"], "skipped"),
                },
            }
        )
    return features


def _collections(result: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    asset_features = _asset_features(result)
    assets_by_id = {asset["asset_id"]: asset for asset in result.assets}
    damage_by_id = {
        feature["properties"]["asset_id"]: feature["properties"]["damage_level"]
        for feature in asset_features
    }
    edge_features = _edge_features(result, assets_by_id, damage_by_id)
    return (
        {"type": "FeatureCollection", "features": asset_features},
        {"type": "FeatureCollection", "features": edge_features},
    )


def render_result_page(result: Any, mapbox_token: str) -> str:
    assets, edges = _collections(result)
    cascade_summary = getattr(result, "cascade_summary", {}) or {}
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TriNetra AI Results</title>
  <link href="https://api.mapbox.com/mapbox-gl-js/v3.4.0/mapbox-gl.css" rel="stylesheet">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Rajdhani:wght@600;700&family=Fira+Code:wght@400;500&display=swap" rel="stylesheet">
  <script src="https://api.mapbox.com/mapbox-gl-js/v3.4.0/mapbox-gl.js"></script>
  <style>
    :root {{
      color-scheme: dark;
      --bg-deep: oklch(3.6% 0.006 35);
      --panel: oklch(5% 0.006 35 / 0.92);
      --panel-2: oklch(10.5% 0.006 35 / 0.94);
      --text: oklch(96% 0.004 35);
      --text-2: oklch(84% 0.004 35);
      --muted: oklch(62% 0.004 35);
      --line: oklch(20% 0.006 35);
      --line-soft: oklch(15% 0.006 35);
      --accent: oklch(61% 0.18 36);
      --accent-glow: oklch(61% 0.18 36 / 0.32);
      --font-display: "Rajdhani", sans-serif;
      --font-mono: "Fira Code", monospace;
      --ease-out: cubic-bezier(0.16, 1, 0.3, 1);
    }}
    *, *::before, *::after {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg-deep);
      color: var(--text);
      font-family: var(--font-mono);
      font-size: 14px;
      line-height: 1.6;
      -webkit-font-smoothing: antialiased;
    }}
    #map {{ position: fixed; inset: 0; }}
    .mapboxgl-ctrl-top-right {{ top: 66px; right: 426px; }}
    .topbar {{
      position: absolute;
      top: 0;
      left: 0;
      right: 0;
      z-index: 3;
      height: 56px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 24px;
      padding: 0 40px;
      background: oklch(4.8% 0.006 35 / 0.88);
      border-bottom: 1px solid oklch(61% 0.18 36 / 0.18);
      pointer-events: none;
    }}
    .brand {{
      display: inline-flex;
      align-items: center;
      min-height: 44px;
      font-family: var(--font-display);
      font-size: 22px;
      font-weight: 700;
      letter-spacing: 0.02em;
      line-height: 1;
      color: var(--text);
    }}
    .run-tag {{
      display: inline-flex;
      align-items: center;
      min-height: 38px;
      border: 1px solid oklch(61% 0.18 36 / 0.42);
      padding: 8px 16px;
      color: var(--accent);
      font-family: var(--font-display);
      font-size: 13px;
      font-weight: 500;
      letter-spacing: 0.02em;
      text-transform: none;
    }}
    .panel {{
      position: absolute;
      top: 74px;
      left: 18px;
      width: min(350px, calc(100vw - 36px));
      max-height: calc(100vh - 92px);
      overflow: auto;
      padding: 18px;
      border: 1px solid var(--line);
      background: var(--panel);
      box-shadow:
        0 0 42px oklch(1% 0.006 35 / 0.88),
        0 24px 80px oklch(1% 0.006 35 / 0.68);
      z-index: 2;
    }}
    .node-summary {{
      position: absolute;
      right: 18px;
      bottom: 18px;
      z-index: 2;
      width: min(350px, calc(100vw - 36px));
      max-height: min(42vh, 360px);
      overflow: auto;
      padding: 18px;
      border: 1px solid var(--line);
      background: var(--panel);
      box-shadow:
        0 0 42px oklch(1% 0.006 35 / 0.88),
        0 24px 80px oklch(1% 0.006 35 / 0.68);
    }}
    .cascade-panel {{
      position: absolute;
      top: 74px;
      right: 18px;
      z-index: 2;
      width: min(350px, calc(100vw - 36px));
      max-height: min(48vh, 440px);
      overflow: auto;
      padding: 18px;
      border: 1px solid var(--line);
      background: var(--panel);
      box-shadow:
        0 0 42px oklch(1% 0.006 35 / 0.88),
        0 24px 80px oklch(1% 0.006 35 / 0.68);
    }}
    h1 {{
      margin: 0 0 4px;
      font-family: var(--font-display);
      font-size: 1.45rem;
      line-height: 1;
      letter-spacing: 0.02em;
    }}
    p {{ margin: 0; color: var(--muted); line-height: 1.5; }}
    .section {{ margin-top: 16px; padding-top: 14px; border-top: 1px solid var(--line-soft); }}
    .section-title {{
      font-family: var(--font-display);
      color: var(--text);
      font-weight: 700;
      letter-spacing: 0.04em;
    }}
    .row {{ display: flex; justify-content: space-between; gap: 12px; align-items: center; margin: 8px 0; }}
    label {{ display: flex; align-items: center; gap: 8px; color: var(--text); }}
    input {{ accent-color: oklch(61% 0.18 36); }}
    .dot {{ width: 8px; height: 8px; transform: rotate(45deg); display: inline-block; flex: 0 0 auto; box-shadow: 0 0 13px currentColor; }}
    .count {{ color: var(--muted); font-variant-numeric: tabular-nums; }}
    .metric-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin: 12px 0 14px; }}
    .metric {{
      min-height: 68px;
      padding: 10px;
      border: 1px solid var(--line-soft);
      background: oklch(8% 0.006 35 / 0.72);
    }}
    .metric strong {{
      display: block;
      font-family: var(--font-display);
      font-size: 1.45rem;
      line-height: 1;
      color: var(--accent);
      font-variant-numeric: tabular-nums;
      text-shadow: 0 0 18px var(--accent-glow);
    }}
    .metric span {{ display: block; margin-top: 6px; color: var(--muted); font-size: 0.68rem; line-height: 1.25; }}
    .cascade-actions {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin: 12px 0; }}
    .cascade-button, .cascade-root {{
      width: 100%;
      border: 1px solid var(--line);
      background: oklch(7% 0.006 35 / 0.82);
      color: var(--text);
      font: inherit;
      text-align: left;
      cursor: pointer;
      transition: border-color 180ms var(--ease-out), background 180ms var(--ease-out), color 180ms var(--ease-out), box-shadow 180ms var(--ease-out);
    }}
    .cascade-button {{
      min-height: 38px;
      padding: 8px 10px;
      color: var(--accent);
      font-family: var(--font-display);
      font-weight: 600;
      text-align: center;
      letter-spacing: 0.02em;
    }}
    .cascade-root {{ display: grid; gap: 5px; margin-top: 8px; padding: 10px; }}
    .cascade-button:hover, .cascade-button.active, .cascade-root:hover, .cascade-root.active {{
      border-color: var(--accent);
      background: oklch(61% 0.18 36 / 0.11);
      box-shadow: 0 0 18px oklch(61% 0.18 36 / 0.16);
    }}
    .cascade-root-title {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      font-family: var(--font-display);
      font-weight: 700;
      line-height: 1.1;
    }}
    .cascade-root-name {{ display: inline-flex; align-items: center; gap: 8px; }}
    .cascade-root-meta {{ color: var(--muted); font-size: 0.72rem; line-height: 1.35; }}
    .cascade-empty {{ color: var(--muted); margin-top: 10px; }}
    .new-run {{
      display: block;
      margin-top: 16px;
      padding: 12px;
      background: transparent;
      border: 1px solid var(--accent);
      color: var(--accent);
      text-decoration: none;
      text-align: center;
      font-family: var(--font-display);
      font-weight: 600;
      letter-spacing: 0.02em;
      transition: background 180ms var(--ease-out), color 180ms var(--ease-out), box-shadow 180ms var(--ease-out);
    }}
    .new-run:hover {{
      background: oklch(61% 0.18 36 / 0.12);
      color: var(--text);
      box-shadow: 0 0 24px var(--accent-glow);
    }}
    .notice {{
      position: absolute;
      top: 50%;
      left: 50%;
      transform: translate(-50%, -50%);
      width: min(420px, calc(100vw - 32px));
      padding: 18px;
      background: var(--panel);
      border: 1px solid var(--line);
      z-index: 3;
    }}
    .mapboxgl-popup-content {{
      width: min(480px, calc(100vw - 40px));
      max-width: calc(100vw - 40px);
      max-height: calc(100vh - 88px);
      overflow: auto;
      padding: 16px;
      border: 1px solid var(--line);
      background: oklch(6.5% 0.006 35 / 0.98);
      color: var(--text);
      box-shadow: 0 18px 48px oklch(1% 0.006 35 / 0.64);
      font-family: var(--font-mono);
    }}
    .mapboxgl-popup-tip {{ border-top-color: oklch(6.5% 0.006 35 / 0.98) !important; }}
    .popup-title {{ margin: 0 0 6px; color: var(--text); font-family: var(--font-display); font-size: 1.25rem; font-weight: 700; }}
    .popup-meta, .popup-small {{ color: var(--muted); font-size: 0.88rem; }}
    .popup-section {{ margin-top: 12px; padding-top: 10px; border-top: 1px solid var(--line-soft); }}
    .popup-heading {{ margin: 0 0 6px; color: var(--text); font-family: var(--font-display); font-weight: 700; letter-spacing: 0.03em; }}
    .popup-list {{ margin: 0; padding-left: 18px; color: var(--muted); }}
    .popup-list li {{ margin: 4px 0; }}
    .prob-row {{ display: flex; justify-content: space-between; gap: 16px; color: var(--muted); font-size: 0.88rem; }}
    .crop-previews {{ display: flex; gap: 8px; margin: 10px 0 2px; }}
    .crop-preview {{ text-align: center; }}
    .crop-preview-label {{ font-size: 0.72rem; color: var(--muted); margin-bottom: 4px; }}
    .crop-preview img {{
      width: 200px;
      height: 200px;
      border: 1px solid var(--line);
      object-fit: cover;
    }}
    @media (max-width: 700px) {{
      .topbar {{ height: 52px; padding: 0 20px; }}
      .run-tag {{ display: none; }}
      .panel {{ top: 66px; max-height: calc(100vh - 84px); }}
      .cascade-panel {{ top: auto; right: 18px; left: 18px; bottom: calc(28vh + 34px); width: auto; max-height: 28vh; }}
      .node-summary {{ position: absolute; right: 18px; left: 18px; bottom: 18px; width: auto; max-height: 28vh; }}
      .mapboxgl-ctrl-top-right {{ top: 60px; right: 18px; }}
      .metric-grid {{ grid-template-columns: 1fr; }}
      .mapboxgl-popup-content {{ width: calc(100vw - 40px); max-height: calc(100vh - 72px); }}
      .crop-previews {{ flex-direction: column; }}
      .crop-preview img {{ width: 100%; height: auto; }}
    }}
  </style>
</head>
<body>
  <div id="map"></div>
  <header class="topbar">
    <div class="brand">TriNetra</div>
    <div class="run-tag">Pipeline Console</div>
  </header>
  <section class="panel">
    <h1>TriNetra AI Results</h1>
    <p>Run: {result.run_id}</p>
    <div class="section">
      <div class="row"><span>Assets</span><span class="count" id="asset-count">0</span></div>
      <div class="row"><span>Edges</span><span class="count" id="edge-count">0</span></div>
    </div>
    <div class="section">
      <p class="section-title">Filter by damage</p>
      <div id="damage-filters"></div>
    </div>
    <div class="section">
      <p class="section-title">Show edges</p>
      <div id="edge-filters"></div>
    </div>
    <a class="new-run" href="/">New Run</a>
  </section>
  <section class="cascade-panel">
    <p class="section-title">Cascade Impact</p>
    <div id="cascade-impact"></div>
    <div id="cascade-roots"></div>
  </section>
  <section class="node-summary">
    <p class="section-title">Node Condition</p>
    <div id="node-condition-summary"></div>
  </section>

  <script>
    const MAPBOX_TOKEN = {_json_for_script(mapbox_token)};
    const ASSETS = {_json_for_script(assets)};
    const EDGES = {_json_for_script(edges)};
    const CASCADE = {_json_for_script(cascade_summary)};
    const DAMAGE_COLORS = {_json_for_script(DAMAGE_COLORS)};
    const DAMAGE_LABELS = {_json_for_script(DAMAGE_LABELS)};
    const EDGE_COLORS = {_json_for_script(EDGE_COLORS)};
    const DAMAGE_ORDER = ["destroyed", "major-damage", "minor-damage", "no-damage", "skipped"];
    const EDGE_ORDER = ["power", "water", "communications"];
    const SQUARE_ASSET_TYPES = ["substation", "cell_tower", "water_treatment"];
    const SQUARE_TYPE_FILTER = ["any", ...SQUARE_ASSET_TYPES.map((type) => ["==", ["get", "asset_type"], type])];
    const CIRCLE_TYPE_FILTER = ["!", SQUARE_TYPE_FILTER];
    const NODE_TYPE_LABELS = {{
      substation: "Substations",
      hospital: "Hospitals",
      water_treatment: "Community Water Systems",
      cell_tower: "Cell Towers",
      shelter: "Shelters",
      fire_station: "Fire Stations",
      police_station: "Police Stations",
      school: "Schools",
      wastewater: "Wastewater Plants",
      fuel_depot: "Fuel Depots",
      data_center: "Data Centers",
      ems_station: "EMS Stations"
    }};

    function propertyInSetExpression(propertyName, values) {{
      const allowedValues = [...values];
      if (!allowedValues.length) return ["==", ["get", propertyName], "__none__"];
      return ["any", ...allowedValues.map((value) => ["==", ["get", propertyName], value])];
    }}

    function damageFilterExpression(visibleDamage) {{
      return propertyInSetExpression("damage_level", visibleDamage);
    }}

    function assetLayerFilter(visibleDamage, typeFilter = null) {{
      const filters = ["all", damageFilterExpression(visibleDamage)];
      if (typeFilter) filters.push(typeFilter);
      return filters;
    }}

    function applyAssetFilters(map, visibleDamage) {{
      if (map.getLayer("assets-glow")) map.setFilter("assets-glow", assetLayerFilter(visibleDamage, CIRCLE_TYPE_FILTER));
      if (map.getLayer("assets-points")) map.setFilter("assets-points", assetLayerFilter(visibleDamage, CIRCLE_TYPE_FILTER));
      if (map.getLayer("assets-square-glow")) map.setFilter("assets-square-glow", assetLayerFilter(visibleDamage, SQUARE_TYPE_FILTER));
      if (map.getLayer("assets-square-points")) map.setFilter("assets-square-points", assetLayerFilter(visibleDamage, SQUARE_TYPE_FILTER));
    }}

    function edgeLayerFilter(edgeType, visibleDamage) {{
      return [
        "all",
        ["==", ["get", "dependency_type"], edgeType],
        propertyInSetExpression("upstream_damage", visibleDamage),
        propertyInSetExpression("downstream_damage", visibleDamage)
      ];
    }}

    function applyEdgeFilters(map, visibleDamage, visibleEdges) {{
      for (const edgeType of EDGE_ORDER) {{
        const layerId = `edges-${{edgeType}}`;
        if (!map.getLayer(layerId)) continue;
        map.setFilter(layerId, edgeLayerFilter(edgeType, visibleDamage));
        map.setLayoutProperty(layerId, "visibility", visibleEdges.has(edgeType) ? "visible" : "none");
      }}
    }}

    function escapeHtml(value) {{
      return String(value ?? "").replace(/[&<>"']/g, (char) => ({{
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        "\\"": "&quot;",
        "'": "&#039;"
      }})[char]);
    }}

    function titleCaseType(type) {{
      return String(type || "unknown").replaceAll("_", " ").replace(/\\b\\w/g, (char) => char.toUpperCase());
    }}

    function formatPct(value) {{
      if (typeof value !== "number" || Number.isNaN(value)) return "n/a";
      return `${{Math.round(value * 100)}}%`;
    }}

    function parseMaybeJson(value, fallback = {{}}) {{
      if (!value) return fallback;
      if (typeof value === "string") {{
        try {{
          return JSON.parse(value);
        }} catch {{
          return fallback;
        }}
      }}
      return value;
    }}

    function damageDot(level) {{
      const color = DAMAGE_COLORS[level] || DAMAGE_COLORS.skipped;
      return `<span class="dot" style="background:${{color}}; color:${{color}}"></span>`;
    }}

    const assetById = new Map(ASSETS.features.map((feature) => [feature.properties.asset_id, feature]));
    const cascadeRootById = new Map((CASCADE.roots || []).map((root) => [root.asset_id, root]));
    const cascadeAffectedIds = new Set(CASCADE.affected_asset_ids || []);
    const cascadeRootIds = new Set(CASCADE.root_asset_ids || []);
    let activeCascadeMode = null;
    let activeCascadeAssetIds = new Set();
    let activeCascadeEdgeKeys = new Set();
    const upstreamOf = new Map();
    const downstreamOf = new Map();
    for (const edge of EDGES.features) {{
      const props = edge.properties;
      if (!upstreamOf.has(props.downstream_asset_id)) upstreamOf.set(props.downstream_asset_id, []);
      upstreamOf.get(props.downstream_asset_id).push(edge);
      if (!downstreamOf.has(props.upstream_asset_id)) downstreamOf.set(props.upstream_asset_id, []);
      downstreamOf.get(props.upstream_asset_id).push(edge);
    }}

    function dependencyLabel(type) {{
      return type === "communications" ? "Comms" : titleCaseType(type);
    }}

    function cascadeAssetIdsForRoot(root) {{
      return new Set([root.asset_id, ...(root.affected_asset_ids || [])]);
    }}

    function cascadeEdgeKeysForRoot(root) {{
      return new Set(root.edge_keys || []);
    }}

    function dependencyCountsText(counts) {{
      const parts = EDGE_ORDER
        .filter((type) => counts?.[type])
        .map((type) => `${{counts[type]}} ${{dependencyLabel(type).toLowerCase()}}`);
      return parts.length ? parts.join(" / ") : "No dependency paths";
    }}

    function cascadeStatusHtml(assetId) {{
      if (cascadeRootIds.has(assetId)) {{
        const root = cascadeRootById.get(assetId);
        return `<div class="popup-section"><p class="popup-heading">Cascade root</p><p class="popup-small">${{root.affected_count}} downstream assets, ${{root.tier_1_downstream_count}} tier-1 facilities at risk.</p></div>`;
      }}
      if (cascadeAffectedIds.has(assetId)) {{
        return `<div class="popup-section"><p class="popup-heading">Cascade exposure</p><p class="popup-small">Potentially downstream of a failed infrastructure provider.</p></div>`;
      }}
      return "";
    }}

    function updateCascadeActiveControls() {{
      document.querySelectorAll("[data-cascade-root], [data-cascade-action]").forEach((button) => {{
        const id = button.dataset.cascadeRoot || button.dataset.cascadeAction;
        button.classList.toggle("active", id === activeCascadeMode);
      }});
    }}

    function applyCascadeStyling(map) {{
      const edgeKeys = [...activeCascadeEdgeKeys];
      const assetIds = [...activeCascadeAssetIds];
      const hasCascade = edgeKeys.length > 0 || assetIds.length > 0;

      for (const type of EDGE_ORDER) {{
        const layerId = `edges-${{type}}`;
        if (!map.getLayer(layerId)) continue;
        if (!hasCascade) {{
          map.setPaintProperty(layerId, "line-opacity", 0.58);
          map.setPaintProperty(layerId, "line-width", 1.45);
        }} else {{
          const selectedEdge = ["in", ["get", "edge_key"], ["literal", edgeKeys]];
          map.setPaintProperty(layerId, "line-opacity", ["case", selectedEdge, 0.96, 0.22]);
          map.setPaintProperty(layerId, "line-width", ["case", selectedEdge, 2.4, 1.15]);
        }}
      }}

      const selectedAsset = ["in", ["get", "asset_id"], ["literal", assetIds]];
      if (map.getLayer("assets-glow")) {{
        map.setPaintProperty("assets-glow", "circle-opacity", hasCascade ? ["case", selectedAsset, 0.62, 0.1] : 0.52);
      }}
      if (map.getLayer("assets-points")) {{
        map.setPaintProperty("assets-points", "circle-opacity", hasCascade ? ["case", selectedAsset, 0.98, 0.32] : 0.98);
      }}
      if (map.getLayer("assets-square-glow")) {{
        map.setPaintProperty("assets-square-glow", "circle-opacity", hasCascade ? ["case", selectedAsset, 0.62, 0.1] : 0.52);
      }}
      if (map.getLayer("assets-square-points")) {{
        map.setPaintProperty("assets-square-points", "icon-opacity", hasCascade ? ["case", selectedAsset, 0.98, 0.32] : 0.98);
      }}
    }}

    function setActiveCascade(map, mode) {{
      activeCascadeMode = mode;
      activeCascadeAssetIds = new Set();
      activeCascadeEdgeKeys = new Set();

      if (mode === "all") {{
        activeCascadeAssetIds = new Set([...(CASCADE.root_asset_ids || []), ...(CASCADE.affected_asset_ids || [])]);
        activeCascadeEdgeKeys = new Set(CASCADE.cascade_edge_keys || []);
      }} else if (mode && cascadeRootById.has(mode)) {{
        const root = cascadeRootById.get(mode);
        activeCascadeAssetIds = cascadeAssetIdsForRoot(root);
        activeCascadeEdgeKeys = cascadeEdgeKeysForRoot(root);
      }} else {{
        activeCascadeMode = null;
      }}

      applyCascadeStyling(map);
      updateCascadeActiveControls();
    }}

    function dependencyList(edges, mode) {{
      if (!edges.length) return `<p class="popup-small">(none)</p>`;
      const rows = edges.slice(0, 8).map((edge) => {{
        const props = edge.properties;
        const relatedId = mode === "upstream" ? props.upstream_asset_id : props.downstream_asset_id;
        const related = assetById.get(relatedId);
        const relatedProps = related?.properties || {{}};
        const damage = relatedProps.damage_level || "skipped";
        return `<li>${{damageDot(damage)}} <strong>${{escapeHtml(dependencyLabel(props.dependency_type))}}:</strong> ${{escapeHtml(relatedProps.name || "Unnamed asset")}} (${{escapeHtml(DAMAGE_LABELS[damage] || damage)}})</li>`;
      }});
      if (edges.length > 8) rows.push(`<li>and ${{edges.length - 8}} more assets</li>`);
      return `<ul class="popup-list">${{rows.join("")}}</ul>`;
    }}

    function probabilitiesHtml(probabilities) {{
      return DAMAGE_ORDER.filter((level) => level !== "skipped").map((level) => `
        <div class="prob-row"><span>${{escapeHtml(DAMAGE_LABELS[level])}}</span><span>${{formatPct(probabilities[level])}}</span></div>
      `).join("");
    }}

    function cropImagesHtml(raw) {{
      if (!raw || !raw.pre_crop_b64 || !raw.post_crop_b64) return "";
      return `
        <div class="crop-previews">
          <div class="crop-preview">
            <div class="crop-preview-label">Pre-disaster</div>
            <img src="data:image/png;base64,${{raw.pre_crop_b64}}" alt="Pre-disaster crop">
          </div>
          <div class="crop-preview">
            <div class="crop-preview-label">Post-disaster</div>
            <img src="data:image/png;base64,${{raw.post_crop_b64}}" alt="Post-disaster crop">
          </div>
        </div>
      `;
    }}

    function popupHtml(feature) {{
      const props = feature.properties || {{}};
      const upstream = upstreamOf.get(props.asset_id) || [];
      const downstream = downstreamOf.get(props.asset_id) || [];
      const level = props.damage_level || "skipped";
      const raw = parseMaybeJson(props.raw);
      const probabilities = parseMaybeJson(props.class_probabilities, raw.class_probabilities || {{}});
      return `
        <p class="popup-title">${{escapeHtml(props.name || "Unnamed asset")}}</p>
        <p class="popup-meta">Type: ${{escapeHtml(titleCaseType(props.asset_type))}} | Tier: ${{escapeHtml(props.criticality_tier || "unknown")}}</p>
        <div class="popup-section">
          <p class="popup-heading">${{damageDot(level)}} DAMAGE: ${{escapeHtml(DAMAGE_LABELS[level] || level)}} (${{formatPct(props.confidence)}} confidence)</p>
          ${{cropImagesHtml(raw)}}
          ${{probabilitiesHtml(probabilities)}}
        </div>
        ${{cascadeStatusHtml(props.asset_id)}}
        <div class="popup-section">
          <p class="popup-heading">Depends on</p>
          ${{dependencyList(upstream, "upstream")}}
        </div>
        <div class="popup-section">
          <p class="popup-heading">Provides to</p>
          ${{dependencyList(downstream, "downstream")}}
        </div>
      `;
    }}

    function countsBy(values, key) {{
      const counts = new Map();
      for (const value of values) {{
        const label = value.properties?.[key] || "unknown";
        counts.set(label, (counts.get(label) || 0) + 1);
      }}
      return counts;
    }}

    function renderNodeConditionSummary() {{
      const counts = new Map();
      for (const feature of ASSETS.features) {{
        const props = feature.properties || {{}};
        const type = props.asset_type || "unknown";
        const level = props.damage_level || "skipped";
        const current = counts.get(type) || {{ stable: 0, total: 0 }};
        current.total += 1;
        if (level === "no-damage" || level === "minor-damage") current.stable += 1;
        counts.set(type, current);
      }}

      const rows = [...counts.entries()]
        .sort((a, b) => b[1].total - a[1].total || a[0].localeCompare(b[0]))
        .map(([type, value]) => {{
          const label = NODE_TYPE_LABELS[type] || `${{titleCaseType(type)}}s`;
          return `<div class="row"><span>${{escapeHtml(label)}}:</span><span class="count">${{value.stable}}/${{value.total}}</span></div>`;
        }});

      document.getElementById("node-condition-summary").innerHTML = rows.join("");
    }}

    function renderCascadePanel(map) {{
      const impact = document.getElementById("cascade-impact");
      const roots = document.getElementById("cascade-roots");
      const rootList = CASCADE.roots || [];

      if (!rootList.length) {{
        impact.innerHTML = `<p class="cascade-empty">No destroyed or major-damage provider has downstream dependencies in this run.</p>`;
        roots.innerHTML = "";
        return;
      }}

      impact.innerHTML = `
        <div class="metric-grid">
          <div class="metric"><strong>${{CASCADE.affected_count || 0}}</strong><span>downstream assets</span></div>
          <div class="metric"><strong>${{CASCADE.tier_1_affected_count || 0}}</strong><span>tier-1 downstream</span></div>
          <div class="metric"><strong>${{CASCADE.max_depth || 0}}</strong><span>max hops</span></div>
        </div>
        <div class="row"><span>Active roots</span><span class="count">${{CASCADE.root_count || rootList.length}}</span></div>
        <div class="row"><span>Path mix</span><span class="count">${{escapeHtml(dependencyCountsText(CASCADE.dependency_counts))}}</span></div>
        <div class="cascade-actions">
          <button class="cascade-button active" type="button" data-cascade-action="all">Highlight All</button>
          <button class="cascade-button" type="button" data-cascade-action="clear">Clear</button>
        </div>
      `;

      roots.innerHTML = rootList.slice(0, 7).map((root) => `
        <button class="cascade-root" type="button" data-cascade-root="${{escapeHtml(root.asset_id)}}">
          <span class="cascade-root-title">
            <span class="cascade-root-name">${{damageDot(root.damage_level)}}<span>${{escapeHtml(root.name)}}</span></span>
            <span class="count">${{root.affected_count}}</span>
          </span>
          <span class="cascade-root-meta">
            ${{escapeHtml(titleCaseType(root.asset_type))}} / ${{escapeHtml(DAMAGE_LABELS[root.damage_level] || root.damage_level)}} /
            ${{root.tier_1_downstream_count}} tier-1 / depth ${{root.max_depth}}
          </span>
        </button>
      `).join("");

      document.querySelectorAll("[data-cascade-action]").forEach((button) => {{
        button.addEventListener("click", () => {{
          const action = button.dataset.cascadeAction;
          setActiveCascade(map, action === "clear" ? null : action);
        }});
      }});

      document.querySelectorAll("[data-cascade-root]").forEach((button) => {{
        button.addEventListener("click", () => {{
          setActiveCascade(map, button.dataset.cascadeRoot);
          const rootFeature = assetById.get(button.dataset.cascadeRoot);
          if (rootFeature) {{
            map.easeTo({{ center: rootFeature.geometry.coordinates, zoom: Math.max(map.getZoom(), 11), duration: 500 }});
          }}
        }});
      }});
    }}

    function renderPanel(map, visibleDamage, visibleEdges) {{
      document.getElementById("asset-count").textContent = String(ASSETS.features.length);
      document.getElementById("edge-count").textContent = String(EDGES.features.length);
      const damageCounts = countsBy(ASSETS.features, "damage_level");
      const edgeCounts = countsBy(EDGES.features, "dependency_type");
      renderNodeConditionSummary();
      renderCascadePanel(map);

      document.getElementById("damage-filters").innerHTML = DAMAGE_ORDER.map((level) => `
        <div class="row">
          <label><input type="checkbox" data-damage="${{escapeHtml(level)}}" ${{visibleDamage.has(level) ? "checked" : ""}}> ${{damageDot(level)}} ${{escapeHtml(DAMAGE_LABELS[level])}}</label>
          <span class="count">${{damageCounts.get(level) || 0}}</span>
        </div>
      `).join("");

      document.getElementById("edge-filters").innerHTML = EDGE_ORDER.map((type) => `
        <div class="row">
          <label><input type="checkbox" data-edge="${{escapeHtml(type)}}" ${{visibleEdges.has(type) ? "checked" : ""}}> <span class="dot" style="background:${{EDGE_COLORS[type]}}; color:${{EDGE_COLORS[type]}}"></span> ${{escapeHtml(dependencyLabel(type))}}</label>
          <span class="count">${{edgeCounts.get(type) || 0}}</span>
        </div>
      `).join("");

      document.querySelectorAll("[data-damage]").forEach((input) => {{
        input.addEventListener("change", (event) => {{
          const level = event.target.dataset.damage;
          if (event.target.checked) visibleDamage.add(level);
          else visibleDamage.delete(level);
          applyAssetFilters(map, visibleDamage);
          applyEdgeFilters(map, visibleDamage, visibleEdges);
        }});
      }});

      document.querySelectorAll("[data-edge]").forEach((input) => {{
        input.addEventListener("change", (event) => {{
          const type = event.target.dataset.edge;
          if (event.target.checked) visibleEdges.add(type);
          else visibleEdges.delete(type);
          applyEdgeFilters(map, visibleDamage, visibleEdges);
        }});
      }});
    }}

    function setDependencyHighlight(map, assetId) {{
      if (!assetId) {{
        applyCascadeStyling(map);
        return;
      }}
      for (const type of EDGE_ORDER) {{
        const layerId = `edges-${{type}}`;
        if (!map.getLayer(layerId)) continue;
        const connected = [
          "any",
          ["==", ["get", "upstream_asset_id"], assetId],
          ["==", ["get", "downstream_asset_id"], assetId]
        ];
        map.setPaintProperty(layerId, "line-opacity", ["case", connected, 0.96, 0.22]);
        map.setPaintProperty(layerId, "line-width", ["case", connected, 2.4, 1.25]);
      }}
    }}

    function fitToAssets(map) {{
      if (!ASSETS.features.length) return;
      const bounds = new mapboxgl.LngLatBounds();
      for (const feature of ASSETS.features) bounds.extend(feature.geometry.coordinates);
      if (!bounds.isEmpty()) map.fitBounds(bounds, {{ padding: 80, maxZoom: 12, duration: 700 }});
    }}

    function hexToRgba(hex, alpha = 1) {{
      const normalized = String(hex || "").replace("#", "");
      const value = normalized.length === 3
        ? normalized.split("").map((char) => char + char).join("")
        : normalized.padEnd(6, "0").slice(0, 6);
      const intValue = Number.parseInt(value, 16);
      const red = (intValue >> 16) & 255;
      const green = (intValue >> 8) & 255;
      const blue = intValue & 255;
      return `rgba(${{red}}, ${{green}}, ${{blue}}, ${{alpha}})`;
    }}

    function squareImageData(color) {{
      const size = 16;
      const canvas = document.createElement("canvas");
      canvas.width = size;
      canvas.height = size;
      const ctx = canvas.getContext("2d");
      const side = 7;
      const inset = (size - side) / 2;
      ctx.fillStyle = hexToRgba(color, 0.98);
      ctx.fillRect(inset, inset, side, side);
      ctx.strokeStyle = "rgba(248, 250, 252, 0.72)";
      ctx.lineWidth = 0.7;
      ctx.strokeRect(inset, inset, side, side);
      const imageData = ctx.getImageData(0, 0, size, size);
      return {{ width: size, height: size, data: imageData.data }};
    }}

    function addSquareImages(map) {{
      for (const [level, color] of Object.entries(DAMAGE_COLORS)) {{
        const coreId = `asset-square-core-${{level}}`;
        if (!map.hasImage(coreId)) map.addImage(coreId, squareImageData(color), {{ pixelRatio: 1 }});
      }}
    }}

    function squareIconExpression(prefix) {{
      return [
        "match", ["get", "damage_level"],
        "destroyed", `${{prefix}}-destroyed`,
        "major-damage", `${{prefix}}-major-damage`,
        "minor-damage", `${{prefix}}-minor-damage`,
        "no-damage", `${{prefix}}-no-damage`,
        `${{prefix}}-skipped`
      ];
    }}

    function boot() {{
      if (!MAPBOX_TOKEN) {{
        const notice = document.createElement("section");
        notice.className = "notice";
        notice.innerHTML = "<strong>Mapbox token missing</strong><p>Set MAPBOX_TOKEN in .env and restart the pipeline server.</p>";
        document.body.appendChild(notice);
        return;
      }}

      mapboxgl.accessToken = MAPBOX_TOKEN;
      const map = new mapboxgl.Map({{
        container: "map",
        style: "mapbox://styles/banksaj/cmofiuev9006301r4etk8eaae",
        center: ASSETS.features[0]?.geometry?.coordinates || [-121.8863, 37.3382],
        zoom: 9,
        pitch: 50,
        bearing: -20,
        antialias: true
      }});
      map.addControl(new mapboxgl.NavigationControl({{ visualizePitch: true }}), "top-right");

      const visibleDamage = new Set(DAMAGE_ORDER.filter((level) => level !== "skipped"));
      const visibleEdges = new Set(EDGE_ORDER.filter((type) => type !== "water"));
      renderPanel(map, visibleDamage, visibleEdges);

      map.on("load", () => {{
        map.addSource("dependencies-source", {{ type: "geojson", data: EDGES }});
        for (const type of EDGE_ORDER) {{
          map.addLayer({{
            id: `edges-${{type}}`,
            type: "line",
            source: "dependencies-source",
            filter: edgeLayerFilter(type, visibleDamage),
            layout: {{ "line-cap": "round", "line-join": "round", "visibility": visibleEdges.has(type) ? "visible" : "none" }},
            paint: {{
              "line-color": EDGE_COLORS[type],
              "line-opacity": 0.58,
              "line-width": 1.45,
              "line-emissive-strength": 1
            }}
          }});
        }}

        map.addSource("assets-source", {{ type: "geojson", data: ASSETS }});
        addSquareImages(map);
        const damageColorExpression = [
          "match", ["get", "damage_level"],
          "destroyed", DAMAGE_COLORS.destroyed,
          "major-damage", DAMAGE_COLORS["major-damage"],
          "minor-damage", DAMAGE_COLORS["minor-damage"],
          "no-damage", DAMAGE_COLORS["no-damage"],
          DAMAGE_COLORS.skipped
        ];
        const squareCoreIconSize = ["match", ["get", "criticality_tier"], 1, 1, 2, 0.8125, 0.65625];
        map.addLayer({{
          id: "assets-glow",
          type: "circle",
          source: "assets-source",
          filter: assetLayerFilter(visibleDamage, CIRCLE_TYPE_FILTER),
          paint: {{
            "circle-color": damageColorExpression,
            "circle-radius": ["match", ["get", "criticality_tier"], 1, 16, 2, 13, 10],
            "circle-blur": 1.25,
            "circle-opacity": 0.52,
            "circle-emissive-strength": 1
          }}
        }});
        map.addLayer({{
          id: "assets-square-glow",
          type: "circle",
          source: "assets-source",
          filter: assetLayerFilter(visibleDamage, SQUARE_TYPE_FILTER),
          paint: {{
            "circle-color": damageColorExpression,
            "circle-radius": ["match", ["get", "criticality_tier"], 1, 16, 2, 13, 10],
            "circle-blur": 1.25,
            "circle-opacity": 0.52,
            "circle-emissive-strength": 1
          }}
        }});
        map.addLayer({{
          id: "assets-points",
          type: "circle",
          source: "assets-source",
          filter: assetLayerFilter(visibleDamage, CIRCLE_TYPE_FILTER),
          paint: {{
            "circle-color": damageColorExpression,
            "circle-radius": ["match", ["get", "criticality_tier"], 1, 3.2, 2, 2.6, 2.1],
            "circle-stroke-color": "rgba(248, 250, 252, 0.72)",
            "circle-stroke-width": 0.7,
            "circle-opacity": 0.98,
            "circle-emissive-strength": 1
          }}
        }});
        map.addLayer({{
          id: "assets-square-points",
          type: "symbol",
          source: "assets-source",
          filter: assetLayerFilter(visibleDamage, SQUARE_TYPE_FILTER),
          layout: {{
            "icon-image": squareIconExpression("asset-square-core"),
            "icon-size": squareCoreIconSize,
            "icon-allow-overlap": true,
            "icon-ignore-placement": true
          }},
          paint: {{
            "icon-opacity": 0.98,
            "icon-emissive-strength": 1
          }}
        }});
        let hoveredAssetId = null;
        function attachAssetInteractions(layerId) {{
          map.on("click", layerId, (event) => {{
            const feature = event.features?.[0];
            if (!feature) return;
            new mapboxgl.Popup({{ closeButton: true, maxWidth: "calc(100vw - 40px)", offset: 18 }})
              .setLngLat(feature.geometry.coordinates)
              .setHTML(popupHtml(feature))
              .addTo(map);
          }});
          map.on("mouseenter", layerId, () => {{ map.getCanvas().style.cursor = "pointer"; }});
          map.on("mouseleave", layerId, () => {{
            map.getCanvas().style.cursor = "";
            hoveredAssetId = null;
            setDependencyHighlight(map, null);
          }});
          map.on("mousemove", layerId, (event) => {{
            const assetId = String(event.features?.[0]?.properties?.asset_id || "");
            if (!assetId || assetId === hoveredAssetId) return;
            hoveredAssetId = assetId;
            setDependencyHighlight(map, assetId);
          }});
        }}
        attachAssetInteractions("assets-points");
        attachAssetInteractions("assets-square-points");
        if ((CASCADE.roots || []).length) setActiveCascade(map, "all");
        fitToAssets(map);
      }});
    }}

    boot();
  </script>
</body>
</html>"""

