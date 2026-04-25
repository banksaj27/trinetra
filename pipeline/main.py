from __future__ import annotations

import json
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse

try:
    from .config import get_settings
    from .map_renderer import render_result_page
    from .runner import get_result, run_pipeline, sse_format
    from .schemas import PipelineRunRequest
except ImportError:  # Support `cd pipeline && uvicorn main:app`.
    from config import get_settings
    from map_renderer import render_result_page
    from runner import get_result, run_pipeline, sse_format
    from schemas import PipelineRunRequest


app = FastAPI(title="TriNetra AI Pipeline")


def _json_for_script(value: object) -> str:
    return json.dumps(value).replace("</", "<\\/")


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    settings = get_settings()
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TriNetra AI Pipeline</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: oklch(14% 0.012 250);
      --panel: oklch(20% 0.018 250);
      --panel-2: oklch(24% 0.018 250);
      --text: oklch(94% 0.008 250);
      --muted: oklch(70% 0.015 250);
      --line: oklch(34% 0.018 250);
      --accent: oklch(67% 0.15 230);
      --accent-strong: oklch(75% 0.17 230);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      background:
        radial-gradient(circle at 20% 10%, oklch(28% 0.07 230 / 0.45), transparent 32rem),
        linear-gradient(145deg, var(--bg), oklch(10% 0.014 260));
      color: var(--text);
    }}
    main {{
      width: min(760px, calc(100vw - 32px));
      padding: 34px;
      border: 1px solid var(--line);
      border-radius: 24px;
      background: color-mix(in oklch, var(--panel) 92%, transparent);
      box-shadow: 0 24px 80px oklch(5% 0.01 250 / 0.38);
    }}
    h1 {{ margin: 0 0 8px; font-size: 2rem; letter-spacing: -0.03em; }}
    p {{ margin: 0; color: var(--muted); line-height: 1.5; }}
    form {{ margin-top: 28px; display: grid; gap: 18px; }}
    label {{ display: grid; gap: 8px; font-size: 0.86rem; color: var(--muted); }}
    input {{
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: var(--panel-2);
      color: var(--text);
      padding: 12px 13px;
      font: inherit;
      outline: none;
    }}
    input:focus {{ border-color: var(--accent); box-shadow: 0 0 0 3px oklch(67% 0.15 230 / 0.18); }}
    .location-row {{ display: grid; grid-template-columns: 1fr auto; gap: 10px; align-items: end; }}
    .grid-3 {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }}
    .grid-2 {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 14px; }}
    button, a.button {{
      border: 0;
      border-radius: 12px;
      background: var(--accent);
      color: oklch(12% 0.014 250);
      padding: 12px 15px;
      font: inherit;
      font-weight: 700;
      cursor: pointer;
      text-decoration: none;
      text-align: center;
    }}
    button:hover, a.button:hover {{ background: var(--accent-strong); }}
    button.secondary {{
      border: 1px solid var(--line);
      background: var(--panel-2);
      color: var(--text);
    }}
    .actions {{ display: flex; justify-content: flex-end; gap: 12px; margin-top: 6px; }}
    .progress {{ display: none; margin-top: 24px; padding-top: 22px; border-top: 1px solid var(--line); }}
    .progress.active {{ display: grid; gap: 12px; }}
    .step {{ color: var(--muted); }}
    .step strong {{ color: var(--text); }}
    .bar {{ height: 8px; border-radius: 999px; background: var(--panel-2); overflow: hidden; }}
    .bar span {{ display: block; height: 100%; width: 0%; background: var(--accent); transition: width 180ms ease-out; }}
    .error {{ color: oklch(76% 0.17 28); }}
    @media (max-width: 680px) {{
      main {{ padding: 24px; }}
      .grid-3, .grid-2, .location-row {{ grid-template-columns: 1fr; }}
      .actions {{ flex-direction: column; }}
    }}
  </style>
</head>
<body>
  <main>
    <h1>TriNetra AI Pipeline</h1>
    <p>Fetch infrastructure assets, infer dependencies, compare imagery, and classify asset damage in one in-memory run.</p>

    <form id="pipeline-form">
      <div class="location-row">
        <label>
          Location
          <input id="location" name="location" placeholder="San Jose, CA" autocomplete="off">
        </label>
        <button class="secondary" id="geocode-button" type="button">Search</button>
      </div>
      <div class="grid-3">
        <label>Latitude <input id="latitude" name="latitude" type="number" step="any" value="37.3382" required></label>
        <label>Longitude <input id="longitude" name="longitude" type="number" step="any" value="-121.8863" required></label>
        <label>Radius (km) <input id="radius_km" name="radius_km" type="number" step="any" min="0.1" value="{settings.DEFAULT_RADIUS_KM}" required></label>
      </div>
      <label>Disaster date <input id="disaster_date" type="date" value="{settings.DEFAULT_DISASTER_DATE}" required></label>
      <div class="actions">
        <button id="run-button" type="submit">Run Pipeline</button>
      </div>
    </form>

    <section class="progress" id="progress-panel" aria-live="polite">
      <div class="bar"><span id="progress-bar"></span></div>
      <p class="step"><strong id="progress-message">Preparing...</strong><br><span id="progress-detail"></span></p>
    </section>
  </main>

  <script>
    const MAPBOX_TOKEN = {_json_for_script(settings.MAPBOX_TOKEN)};

    async function geocode(query) {{
      if (!query || !MAPBOX_TOKEN) return;
      const response = await fetch(
        `https://api.mapbox.com/geocoding/v5/mapbox.places/${{encodeURIComponent(query)}}.json?access_token=${{MAPBOX_TOKEN}}&limit=1`
      );
      const data = await response.json();
      if (data.features && data.features.length > 0) {{
        const [lng, lat] = data.features[0].center;
        document.getElementById("latitude").value = lat.toFixed(4);
        document.getElementById("longitude").value = lng.toFixed(4);
      }}
    }}

    function setProgress(data) {{
      document.getElementById("progress-panel").classList.add("active");
      document.getElementById("progress-message").textContent = data.message || "Working...";
      document.getElementById("progress-detail").textContent = data.detail || "";
      const pct = Math.max(0, Math.min(100, ((data.step || 0) / (data.total_steps || 5)) * 100));
      document.getElementById("progress-bar").style.width = `${{pct}}%`;
    }}

    async function consumeSse(response) {{
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {{
        const {{ value, done }} = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, {{ stream: true }});
        const events = buffer.split("\\n\\n");
        buffer = events.pop() || "";
        for (const rawEvent of events) {{
          const lines = rawEvent.split("\\n");
          const eventName = (lines.find((line) => line.startsWith("event:")) || "").slice(6).trim();
          const dataLine = lines.find((line) => line.startsWith("data:"));
          if (!dataLine) continue;
          const data = JSON.parse(dataLine.slice(5));
          if (eventName === "status") setProgress(data);
          if (eventName === "error") {{
            setProgress({{ step: 5, total_steps: 5, message: "Pipeline failed", detail: data.detail || data.message }});
            document.getElementById("progress-detail").classList.add("error");
          }}
          if (eventName === "complete" && data.redirect) {{
            window.location.href = data.redirect;
          }}
        }}
      }}
    }}

    document.getElementById("geocode-button").addEventListener("click", () => {{
      geocode(document.getElementById("location").value);
    }});

    document.getElementById("pipeline-form").addEventListener("submit", async (event) => {{
      event.preventDefault();
      document.getElementById("run-button").disabled = true;
      setProgress({{ step: 0, total_steps: 5, message: "Starting pipeline", detail: "Preparing request" }});
      const payload = {{
        latitude: Number(document.getElementById("latitude").value),
        longitude: Number(document.getElementById("longitude").value),
        radius_km: Number(document.getElementById("radius_km").value),
        disaster_date: document.getElementById("disaster_date").value
      }};
      const response = await fetch("/api/v1/run", {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify(payload)
      }});
      if (!response.ok || !response.body) throw new Error(`Pipeline request failed with status ${{response.status}}`);
      await consumeSse(response);
    }});
  </script>
</body>
</html>"""
    return HTMLResponse(html)


@app.post("/api/v1/run")
async def run(request: PipelineRunRequest) -> StreamingResponse:
    async def event_stream() -> AsyncIterator[str]:
        try:
            async for event in run_pipeline(request):
                yield sse_format(event)
        except Exception as exc:
            yield sse_format(
                {
                    "event": "error",
                    "data": {
                        "step": 5,
                        "total_steps": 5,
                        "message": "Pipeline failed",
                        "detail": str(exc),
                    },
                }
            )

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/v1/results/{run_id}", response_class=HTMLResponse)
async def results(run_id: str) -> HTMLResponse:
    result = get_result(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Pipeline result not found or expired")
    return HTMLResponse(render_result_page(result, get_settings().MAPBOX_TOKEN))


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}

