"""Eye 3 Agent — Cascade Orchestration Layer."""
import os
from uagents import Agent, Context
from shared_models import Eye3Query, Eye3Response


def _load_seed() -> str:
    seed = os.environ.get("EYE3_SEED")
    if seed:
        return seed
    # fallback: read from .env manually (no python-dotenv)
    try:
        with open(".env") as f:
            for line in f:
                if line.startswith("EYE3_SEED="):
                    return line.split("=", 1)[1].strip()
    except FileNotFoundError:
        pass
    return "trinetra-eye3-default-seed-change-me"


agent = Agent(
    name="trinetra-eye3",
    seed=_load_seed(),
    port=8004,
    mailbox=True,
)


@agent.on_event("startup")
async def startup(ctx: Context):
    ctx.logger.info(f"Eye 3 Agent online. Address: {agent.address}")


@agent.on_message(model=Eye3Query)
async def handle_query(ctx: Context, sender: str, msg: Eye3Query):
    ctx.logger.info(f"Eye 3 received: {msg.query_type} from {sender}")

    if msg.query_type == "ping":
        await ctx.send(sender, Eye3Response(
            ok=True,
            source="eye3",
            message="Eye 3 online — cascade orchestration ready.",
            data={"status": "ready"},
        ))
        return

    await ctx.send(sender, Eye3Response(
        ok=False,
        source="eye3",
        message=f"Unknown query_type: {msg.query_type}",
    ))


if __name__ == "__main__":
    agent.run()
