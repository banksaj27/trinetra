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
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TriNetra AI Results</title>
  <link href="https://api.mapbox.com/mapbox-gl-js/v3.4.0/mapbox-gl.css" rel="stylesheet">
  <script src="https://api.mapbox.com/mapbox-gl-js/v3.4.0/mapbox-gl.js"></script>
  <style>
    :root {{
      color-scheme: dark;
      --panel: oklch(17% 0.014 250 / 0.94);
      --panel-2: oklch(23% 0.018 250 / 0.94);
      --text: oklch(94% 0.008 250);
      --muted: oklch(70% 0.015 250);
      --line: oklch(34% 0.018 250);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
    }}
    body {{ margin: 0; background: oklch(10% 0.014 250); color: var(--text); }}
    #map {{ position: fixed; inset: 0; }}
    .panel {{
      position: absolute;
      top: 18px;
      left: 18px;
      width: min(330px, calc(100vw - 36px));
      max-height: calc(100vh - 36px);
      overflow: auto;
      padding: 18px;
      border: 1px solid var(--line);
      border-radius: 18px;
      background: var(--panel);
      box-shadow: 0 22px 60px oklch(4% 0.01 250 / 0.42);
      z-index: 2;
    }}
    h1 {{ margin: 0 0 4px; font-size: 1.1rem; letter-spacing: -0.02em; }}
    p {{ margin: 0; color: var(--muted); line-height: 1.45; }}
    .section {{ margin-top: 16px; padding-top: 14px; border-top: 1px solid var(--line); }}
    .row {{ display: flex; justify-content: space-between; gap: 12px; align-items: center; margin: 8px 0; }}
    label {{ display: flex; align-items: center; gap: 8px; color: var(--text); }}
    input {{ accent-color: #38BDF8; }}
    .dot {{ width: 9px; height: 9px; border-radius: 999px; display: inline-block; flex: 0 0 auto; }}
    .count {{ color: var(--muted); font-variant-numeric: tabular-nums; }}
    .new-run {{
      display: block;
      margin-top: 16px;
      padding: 10px 12px;
      border-radius: 12px;
      background: var(--panel-2);
      border: 1px solid var(--line);
      color: var(--text);
      text-decoration: none;
      text-align: center;
      font-weight: 700;
    }}
    .notice {{
      position: absolute;
      top: 50%;
      left: 50%;
      transform: translate(-50%, -50%);
      width: min(420px, calc(100vw - 32px));
      padding: 18px;
      border-radius: 18px;
      background: var(--panel);
      border: 1px solid var(--line);
      z-index: 3;
    }}
    .mapboxgl-popup-content {{
      min-width: 440px;
      max-width: 480px;
      padding: 16px;
      border-radius: 14px;
      background: oklch(18% 0.014 250);
      color: var(--text);
      box-shadow: 0 18px 40px oklch(3% 0.01 250 / 0.5);
    }}
    .mapboxgl-popup-tip {{ border-top-color: oklch(18% 0.014 250) !important; }}
    .popup-title {{ margin: 0 0 6px; color: var(--text); font-weight: 800; }}
    .popup-meta, .popup-small {{ color: var(--muted); font-size: 0.88rem; }}
    .popup-section {{ margin-top: 12px; padding-top: 10px; border-top: 1px solid var(--line); }}
    .popup-heading {{ margin: 0 0 6px; color: var(--text); font-weight: 700; }}
    .popup-list {{ margin: 0; padding-left: 18px; color: var(--muted); }}
    .popup-list li {{ margin: 4px 0; }}
    .prob-row {{ display: flex; justify-content: space-between; gap: 16px; color: var(--muted); font-size: 0.88rem; }}
    .crop-previews {{ display: flex; gap: 8px; margin: 10px 0 2px; }}
    .crop-preview {{ text-align: center; }}
    .crop-preview-label {{ font-size: 0.72rem; color: var(--muted); margin-bottom: 4px; }}
    .crop-preview img {{
      width: 200px;
      height: 200px;
      border-radius: 8px;
      border: 1px solid var(--line);
      object-fit: cover;
    }}
  </style>
</head>
<body>
  <div id="map"></div>
  <section class="panel">
    <h1>TriNetra AI Results</h1>
    <p>Run: {result.run_id}</p>
    <div class="section">
      <div class="row"><span>Assets</span><span class="count" id="asset-count">0</span></div>
      <div class="row"><span>Edges</span><span class="count" id="edge-count">0</span></div>
    </div>
    <div class="section">
      <p><strong>Damage Summary</strong></p>
      <div id="damage-summary"></div>
    </div>
    <div class="section">
      <p><strong>Filter by damage</strong></p>
      <div id="damage-filters"></div>
    </div>
    <div class="section">
      <p><strong>Show edges</strong></p>
      <div id="edge-filters"></div>
    </div>
    <a class="new-run" href="/">New Run</a>
  </section>

  <script>
    const MAPBOX_TOKEN = {_json_for_script(mapbox_token)};
    const ASSETS = {_json_for_script(assets)};
    const EDGES = {_json_for_script(edges)};
    const DAMAGE_COLORS = {_json_for_script(DAMAGE_COLORS)};
    const DAMAGE_LABELS = {_json_for_script(DAMAGE_LABELS)};
    const EDGE_COLORS = {_json_for_script(EDGE_COLORS)};
    const DAMAGE_ORDER = ["destroyed", "major-damage", "minor-damage", "no-damage", "skipped"];
    const EDGE_ORDER = ["power", "water", "communications"];

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
      return `<span class="dot" style="background:${{DAMAGE_COLORS[level] || DAMAGE_COLORS.skipped}}"></span>`;
    }}

    const assetById = new Map(ASSETS.features.map((feature) => [feature.properties.asset_id, feature]));
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

    function renderPanel(map, visibleDamage, visibleEdges) {{
      document.getElementById("asset-count").textContent = String(ASSETS.features.length);
      document.getElementById("edge-count").textContent = String(EDGES.features.length);
      const damageCounts = countsBy(ASSETS.features, "damage_level");
      const edgeCounts = countsBy(EDGES.features, "dependency_type");

      document.getElementById("damage-summary").innerHTML = DAMAGE_ORDER.map((level) => `
        <div class="row"><span>${{damageDot(level)}} ${{escapeHtml(DAMAGE_LABELS[level])}}</span><span class="count">${{damageCounts.get(level) || 0}}</span></div>
      `).join("");

      document.getElementById("damage-filters").innerHTML = DAMAGE_ORDER.map((level) => `
        <div class="row">
          <label><input type="checkbox" data-damage="${{escapeHtml(level)}}" checked> ${{damageDot(level)}} ${{escapeHtml(DAMAGE_LABELS[level])}}</label>
          <span class="count">${{damageCounts.get(level) || 0}}</span>
        </div>
      `).join("");

      document.getElementById("edge-filters").innerHTML = EDGE_ORDER.map((type) => `
        <div class="row">
          <label><input type="checkbox" data-edge="${{escapeHtml(type)}}" checked> <span class="dot" style="background:${{EDGE_COLORS[type]}}"></span> ${{escapeHtml(dependencyLabel(type))}}</label>
          <span class="count">${{edgeCounts.get(type) || 0}}</span>
        </div>
      `).join("");

      document.querySelectorAll("[data-damage]").forEach((input) => {{
        input.addEventListener("change", (event) => {{
          const level = event.target.dataset.damage;
          if (event.target.checked) visibleDamage.add(level);
          else visibleDamage.delete(level);
          const filter = ["in", ["get", "damage_level"], ["literal", [...visibleDamage]]];
          if (map.getLayer("assets-glow")) map.setFilter("assets-glow", filter);
          if (map.getLayer("assets-points")) map.setFilter("assets-points", filter);
        }});
      }});

      document.querySelectorAll("[data-edge]").forEach((input) => {{
        input.addEventListener("change", (event) => {{
          const type = event.target.dataset.edge;
          if (event.target.checked) visibleEdges.add(type);
          else visibleEdges.delete(type);
          for (const edgeType of EDGE_ORDER) {{
            const layerId = `edges-${{edgeType}}`;
            if (map.getLayer(layerId)) {{
              map.setLayoutProperty(layerId, "visibility", visibleEdges.has(edgeType) ? "visible" : "none");
            }}
          }}
        }});
      }});
    }}

    function setDependencyHighlight(map, assetId) {{
      for (const type of EDGE_ORDER) {{
        const layerId = `edges-${{type}}`;
        if (!map.getLayer(layerId)) continue;
        if (!assetId) {{
          map.setPaintProperty(layerId, "line-opacity", 0.15);
          map.setPaintProperty(layerId, "line-width", 1);
          continue;
        }}
        const connected = [
          "any",
          ["==", ["get", "upstream_asset_id"], assetId],
          ["==", ["get", "downstream_asset_id"], assetId]
        ];
        map.setPaintProperty(layerId, "line-opacity", ["case", connected, 0.7, 0.05]);
        map.setPaintProperty(layerId, "line-width", ["case", connected, 2, 1]);
      }}
    }}

    function fitToAssets(map) {{
      if (!ASSETS.features.length) return;
      const bounds = new mapboxgl.LngLatBounds();
      for (const feature of ASSETS.features) bounds.extend(feature.geometry.coordinates);
      if (!bounds.isEmpty()) map.fitBounds(bounds, {{ padding: 80, maxZoom: 12, duration: 700 }});
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
        style: "mapbox://styles/mapbox/dark-v11",
        center: ASSETS.features[0]?.geometry?.coordinates || [-121.8863, 37.3382],
        zoom: 9,
        pitch: 50,
        bearing: -20,
        antialias: true
      }});
      map.addControl(new mapboxgl.NavigationControl({{ visualizePitch: true }}), "top-right");

      const visibleDamage = new Set(DAMAGE_ORDER);
      const visibleEdges = new Set(EDGE_ORDER);
      renderPanel(map, visibleDamage, visibleEdges);

      map.on("load", () => {{
        map.addSource("dependencies-source", {{ type: "geojson", data: EDGES }});
        for (const type of EDGE_ORDER) {{
          map.addLayer({{
            id: `edges-${{type}}`,
            type: "line",
            source: "dependencies-source",
            filter: ["==", ["get", "dependency_type"], type],
            layout: {{ "line-cap": "round", "line-join": "round" }},
            paint: {{
              "line-color": EDGE_COLORS[type],
              "line-opacity": 0.15,
              "line-width": 1,
              "line-emissive-strength": 1
            }}
          }});
        }}

        map.addSource("assets-source", {{ type: "geojson", data: ASSETS }});
        const damageColorExpression = [
          "match", ["get", "damage_level"],
          "destroyed", DAMAGE_COLORS.destroyed,
          "major-damage", DAMAGE_COLORS["major-damage"],
          "minor-damage", DAMAGE_COLORS["minor-damage"],
          "no-damage", DAMAGE_COLORS["no-damage"],
          DAMAGE_COLORS.skipped
        ];
        map.addLayer({{
          id: "assets-glow",
          type: "circle",
          source: "assets-source",
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
          paint: {{
            "circle-color": damageColorExpression,
            "circle-radius": ["match", ["get", "criticality_tier"], 1, 3.2, 2, 2.6, 2.1],
            "circle-stroke-color": "rgba(248, 250, 252, 0.72)",
            "circle-stroke-width": 0.7,
            "circle-opacity": 0.98,
            "circle-emissive-strength": 1
          }}
        }});
        map.on("click", "assets-points", (event) => {{
          const feature = event.features?.[0];
          if (!feature) return;
          new mapboxgl.Popup({{ closeButton: true }})
            .setLngLat(feature.geometry.coordinates)
            .setHTML(popupHtml(feature))
            .addTo(map);
        }});
        map.on("mouseenter", "assets-points", () => {{ map.getCanvas().style.cursor = "pointer"; }});
        map.on("mouseleave", "assets-points", () => {{
          map.getCanvas().style.cursor = "";
          setDependencyHighlight(map, null);
        }});
        let hoveredAssetId = null;
        map.on("mousemove", "assets-points", (event) => {{
          const assetId = String(event.features?.[0]?.properties?.asset_id || "");
          if (!assetId || assetId === hoveredAssetId) return;
          hoveredAssetId = assetId;
          setDependencyHighlight(map, assetId);
        }});
        fitToAssets(map);
      }});
    }}

    boot();
  </script>
</body>
</html>"""

