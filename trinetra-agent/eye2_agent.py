"""Eye 2 Agent — Disaster Heuristics Layer."""
import os
from uagents import Agent, Context
from shared_models import Eye2Query, Eye2Response


HEURISTICS = {
    "wildfire": {
        "spread_rate": "fast (1-2 miles/hour in dry conditions)",
        "primary_risk": "structure ignition and air quality degradation",
        "cascade_likelihood": 0.82,
    },
    "flood": {
        "spread_rate": "moderate (hours to days depending on watershed)",
        "primary_risk": "infrastructure submersion and road network failure",
        "cascade_likelihood": 0.74,
    },
    "earthquake": {
        "spread_rate": "instantaneous (seconds for primary shockwave)",
        "primary_risk": "structural collapse and utility line rupture",
        "cascade_likelihood": 0.91,
    },
    "hurricane": {
        "spread_rate": "slow (days of warning, 10-15 mph forward speed)",
        "primary_risk": "wind damage and storm surge flooding",
        "cascade_likelihood": 0.88,
    },
}


def _load_seed() -> str:
    seed = os.environ.get("EYE2_SEED")
    if seed:
        return seed
    # fallback: read from .env manually (no python-dotenv)
    try:
        with open(".env") as f:
            for line in f:
                if line.startswith("EYE2_SEED="):
                    return line.split("=", 1)[1].strip()
    except FileNotFoundError:
        pass
    return "trinetra-eye2-default-seed-change-me"


agent = Agent(
    name="trinetra-eye2",
    seed=_load_seed(),
    port=8003,
    mailbox=True,
)


@agent.on_event("startup")
async def startup(ctx: Context):
    ctx.logger.info(f"Eye 2 Agent online. Address: {agent.address}")


@agent.on_message(model=Eye2Query)
async def handle_query(ctx: Context, sender: str, msg: Eye2Query):
    ctx.logger.info(f"Eye 2 received: {msg.query_type} from {sender}")

    if msg.query_type == "ping":
        await ctx.send(sender, Eye2Response(
            ok=True,
            source="eye2",
            message="Eye 2 online — 4 disaster heuristic models loaded.",
            data={"models": list(HEURISTICS.keys())},
        ))
        return

    if msg.query_type == "heuristic_lookup" and msg.disaster_type:
        key = msg.disaster_type.lower()
        heuristic = HEURISTICS.get(key)
        if heuristic:
            await ctx.send(sender, Eye2Response(
                ok=True,
                source="eye2",
                message=f"Heuristic found for '{key}'.",
                data=heuristic,
            ))
        else:
            await ctx.send(sender, Eye2Response(
                ok=False,
                source="eye2",
                message=f"No heuristic for '{msg.disaster_type}'.",
            ))
        return

    await ctx.send(sender, Eye2Response(
        ok=False,
        source="eye2",
        message=f"Unknown query_type: {msg.query_type}",
    ))


if __name__ == "__main__":
    agent.run()
