"""Eye 1 Agent — Asset Detection Layer (stubbed for demo)."""
import os
from uagents import Agent, Context
from shared_models import Eye1Query, Eye1Response


def _load_seed() -> str:
    seed = os.environ.get("EYE1_SEED")
    if seed:
        return seed
    # fallback: read from .env manually (no python-dotenv)
    try:
        with open(".env") as f:
            for line in f:
                if line.startswith("EYE1_SEED="):
                    return line.split("=", 1)[1].strip()
    except FileNotFoundError:
        pass
    return "trinetra-eye1-default-seed-change-me"


agent = Agent(
    name="trinetra-eye1",
    seed=_load_seed(),
    port=8002,
    mailbox=True,
)


@agent.on_event("startup")
async def startup(ctx: Context):
    ctx.logger.info(f"Eye 1 Agent online. Address: {agent.address}")


@agent.on_message(model=Eye1Query)
async def handle_query(ctx: Context, sender: str, msg: Eye1Query):
    ctx.logger.info(f"Eye 1 receid: {msg.query_type} from {sender}")

    if msg.query_type == "ping":
        await ctx.send(sender, Eye1Response(
            ok=True,
            source="eye1",
            message="Eye 1 online — 617 Puerto Rico assets indexed.",
            data={"asset_count": 617},
        ))
        return

    if msg.query_type == "asset_lookup" and msg.asset_name:
        await ctx.send(sender, Eye1Response(
            ok=True,
            source="eye1",
            message=f"Asset '{msg.asset_name}' found in Eye 1 index.",
            data={
                "asset_name": msg.asset_name,
                "asset_type": "infrastructure",
                "status": "operational",
                "criticality": "high",
            },
        ))
        return

    await ctx.send(sender, Eye1Response(
        ok=False,
        source="eye1",
        message=f"Unknown query_type: {msg.query_type}",
    ))


if __name__ == "__main__":
    agent.run()
