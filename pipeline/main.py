from __future__ import annotations

import json
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse

if __package__:
    from .config import get_settings
    from .map_renderer import render_result_page
    from .runner import get_result, run_pipeline, sse_format
    from .schemas import PipelineRunRequest
else:  # Support `cd pipeline && uvicorn main:app`.
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
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Rajdhani:wght@600;700&family=Fira+Code:wght@400;500&display=swap" rel="stylesheet">
  <style>
    :root {{
      color-scheme: dark;
      --bg-deep: oklch(3.6% 0.006 35);
      --bg: oklch(6.5% 0.006 35);
      --panel: oklch(8.4% 0.006 35);
      --panel-2: oklch(11% 0.006 35);
      --line: oklch(20% 0.006 35);
      --line-soft: oklch(15% 0.006 35);
      --text: oklch(96% 0.004 35);
      --text-2: oklch(84% 0.004 35);
      --muted: oklch(62% 0.004 35);
      --accent: oklch(61% 0.18 36);
      --accent-glow: oklch(61% 0.18 36 / 0.32);
      --font-display: "Rajdhani", sans-serif;
      --font-mono: "Fira Code", monospace;
      --ease-out: cubic-bezier(0.16, 1, 0.3, 1);
    }}
    *, *::before, *::after {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      padding: 56px 0 0;
      background:
        radial-gradient(ellipse 55% 42% at 78% 68%, oklch(61% 0.18 36 / 0.12), transparent 72%),
        linear-gradient(180deg, var(--bg-deep) 0%, var(--bg) 100%);
      color: var(--text);
      font-family: var(--font-mono);
      font-size: 15px;
      line-height: 1.65;
      -webkit-font-smoothing: antialiased;
    }}
    .topbar {{
      position: fixed;
      top: 0;
      left: 0;
      right: 0;
      z-index: 10;
      height: 56px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 24px;
      padding: 0 40px;
      background: oklch(4.8% 0.006 35 / 0.9);
      border-bottom: 1px solid oklch(61% 0.18 36 / 0.18);
    }}
    .brand {{
      display: inline-flex;
      align-items: center;
      gap: 0.55rem;
      min-height: 44px;
      font-family: var(--font-display);
      font-size: 22px;
      letter-spacing: 0.02em;
      line-height: 1;
      color: var(--text);
      align-self: center;
    }}
    .status-tag {{
      min-height: 38px;
      display: inline-flex;
      align-items: center;
      border: 1px solid oklch(61% 0.18 36 / 0.42);
      padding: 8px 16px;
      color: var(--accent);
      font-family: var(--font-display);
      font-size: 13px;
      font-weight: 500;
      letter-spacing: 0.02em;
      text-transform: none;
    }}
    .shell {{
      min-height: calc(100vh - 56px);
      display: grid;
      place-items: center;
      padding: clamp(28px, 5vw, 64px) 20px;
      background:
        linear-gradient(90deg, transparent 0 49%, oklch(61% 0.18 36 / 0.08) 49% 49.1%, transparent 49.1% 100%),
        repeating-linear-gradient(0deg, transparent 0 72px, oklch(96% 0.004 35 / 0.035) 72px 73px);
      mask-image: linear-gradient(90deg, transparent, var(--text) 14%, var(--text) 86%, transparent);
    }}
    main {{
      width: min(840px, calc(100vw - 32px));
      padding: clamp(24px, 4vw, 38px);
      border: 1px solid var(--line);
      background: oklch(5% 0.006 35 / 0.9);
      box-shadow: 0 28px 80px oklch(1% 0.006 35 / 0.58);
    }}
    h1 {{
      margin: 0;
      font-family: var(--font-display);
      font-size: clamp(2.1rem, 5vw, 3.1rem);
      line-height: 0.98;
      letter-spacing: 0.01em;
      color: var(--text);
    }}
    p {{ margin: 0; max-width: 68ch; color: var(--text-2); line-height: 1.72; }}
    form {{
      margin-top: 20px;
      display: grid;
      gap: 18px;
      padding-top: 20px;
      border-top: 1px solid var(--line-soft);
    }}
    label {{
      display: grid;
      gap: 8px;
      color: var(--muted);
      font-size: 0.74rem;
      letter-spacing: 0.07em;
      text-transform: uppercase;
    }}
    input {{
      width: 100%;
      border: 1px solid var(--line);
      background: var(--panel-2);
      color: var(--text);
      padding: 13px 14px;
      font: inherit;
      outline: none;
      min-height: 48px;
    }}
    input::placeholder {{ color: oklch(50% 0.004 35); }}
    input:focus {{
      border-color: var(--accent);
      box-shadow: 0 0 0 3px oklch(61% 0.18 36 / 0.14);
    }}
    .location-row {{ display: grid; grid-template-columns: 1fr auto; gap: 12px; align-items: end; }}
    .grid-3 {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }}
    .grid-2 {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 14px; }}
    button, a.button {{
      min-height: 48px;
      border: 1px solid var(--accent);
      background: var(--accent);
      color: var(--text);
      padding: 12px 18px;
      font-family: var(--font-display);
      font-size: 0.95rem;
      font-weight: 600;
      letter-spacing: 0.02em;
      cursor: pointer;
      text-decoration: none;
      text-align: center;
      transition: background 180ms var(--ease-out), box-shadow 180ms var(--ease-out), border-color 180ms var(--ease-out);
    }}
    button:hover, a.button:hover {{ box-shadow: 0 0 24px var(--accent-glow); }}
    button:disabled {{ cursor: wait; opacity: 0.68; box-shadow: none; }}
    button.secondary {{
      background: transparent;
      color: var(--accent);
    }}
    button.secondary:hover {{ background: oklch(61% 0.18 36 / 0.12); color: var(--text); }}
    .actions {{ display: flex; justify-content: flex-end; gap: 12px; margin-top: 8px; }}
    .progress {{ display: none; margin-top: 24px; padding-top: 22px; border-top: 1px solid var(--line); }}
    .progress.active {{ display: grid; gap: 12px; }}
    .step {{ color: var(--muted); }}
    .step strong {{ color: var(--text); }}
    .bar {{ height: 4px; background: var(--panel-2); overflow: hidden; }}
    .bar span {{
      display: block;
      height: 100%;
      width: 0%;
      background: linear-gradient(90deg, oklch(55% 0.14 36), var(--accent), oklch(74% 0.12 52));
      background-size: 180% 100%;
      box-shadow: 0 0 18px var(--accent-glow);
      transition: width 220ms var(--ease-out);
    }}
    .error {{ color: oklch(68% 0.2 35); }}
    @media (max-width: 680px) {{
      body {{ padding-top: 52px; }}
      .topbar {{ height: 52px; padding: 0 20px; }}
      .status-tag {{ display: none; }}
      main {{ padding: 24px; }}
      .grid-3, .grid-2, .location-row {{ grid-template-columns: 1fr; }}
      .actions {{ flex-direction: column; }}
    }}
  </style>
</head>
<body>
  <header class="topbar">
    <div class="brand">TriNetra</div>
    <div class="status-tag">Pipeline Console</div>
  </header>
  <div class="shell">
  <main>
    <h1>TriNetra Pipeline Console</h1>

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
      <div class="grid-2">
        <label>Disaster date <input id="disaster_date" type="date" value="{settings.DEFAULT_DISASTER_DATE}" required></label>
        <label style="pointer-events:none; opacity:0.55;">
          Event type (auto-detected)
          <input id="event_type_display" placeholder="Detected after submit" readonly tabindex="-1">
        </label>
      </div>
      <div class="actions">
        <button id="run-button" type="submit">Run Pipeline</button>
      </div>
    </form>

    <section class="progress" id="progress-panel" aria-live="polite">
      <div class="bar"><span id="progress-bar"></span></div>
      <p class="step"><strong id="progress-message">Preparing...</strong><br><span id="progress-detail"></span></p>
    </section>
  </main>
  </div>

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
      const pct = Math.max(0, Math.min(100, ((data.step || 0) / (data.total_steps || 7)) * 100));
      document.getElementById("progress-bar").style.width = `${{pct > 0 ? pct : 6}}%`;
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
          if (eventName === "status") {{
            setProgress(data);
            if (data.step === 1 && data.detail && data.detail.startsWith("Detected:")) {{
              const match = data.detail.match(/Detected:\\s*(\\S+)/u);
              if (match) document.getElementById("event_type_display").value = match[1];
            }}
          }}
          if (eventName === "error") {{
            setProgress({{ step: 7, total_steps: 7, message: "Pipeline failed", detail: data.detail || data.message }});
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
      setProgress({{ step: 0, total_steps: 7, message: "Starting pipeline", detail: "Preparing request" }});
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
                        "step": 7,
                        "total_steps": 7,
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

