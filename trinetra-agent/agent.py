"""TriNetra cascade agent — ASI:One Chat Protocol entry point."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    EndSessionContent,
    TextContent,
    chat_protocol_spec,
)

import config
import formatter
import router
from api_client import TPIError, TriNetraClient
from shared_models import Eye1Query, Eye1Response, Eye2Query, Eye2Response, Eye3Query, Eye3Response, CascadeContext

config.validate()

_eye_state: dict = {"eye1": None, "eye2": None, "eye3": None}

EYE1_AGENT_ADDRESS = "agent1qwngtn9jy6ktv4ltf4k0j2asm69tvjccwnrpsn7dy3aup7thxw7d5vtj5xs"
EYE2_AGENT_ADDRESS = "agent1qdd3hdhlvcxy665urxa6kqzyga8jre7jc3l05v7qtedmx69v3ueg26s2pr3"
EYE3_AGENT_ADDRESS = "agent1qwkpaz8l5t4fxlq9uncv4fk5gtnmgk87neuxdwkzygu4cze327kjjzl6cr6"

agent = Agent(
    name=config.AGENT_NAME,
    seed=config.AGENT_SEED,
    port=config.AGENT_PORT,
    mailbox=True,
    publish_agent_details=True,
)

protocol = Protocol(spec=chat_protocol_spec)
api = TriNetraClient()


def _extract_text(msg: ChatMessage) -> str:
    parts: list[str] = []
    for item in msg.content or []:
        text = getattr(item, "text", None)
        if isinstance(text, str) and text:
            parts.append(text)
    return " ".join(parts).strip()


async def _dispatch(routed: router.RoutedQuery, raw_text: str) -> str:
    intent = routed.intent
    params = routed.params

    if intent == "help":
        return await formatter.format_response("help", None, raw_text)

    if intent == "priorities":
        data = await api.get_priorities(limit=params.get("limit", 10))
        return await formatter.format_response("priorities", data, raw_text)

    if intent == "lookup":
        data = await api.get_cascade(params["cascade_id"])
        return await formatter.format_response("lookup", data, raw_text)

    if intent == "cascade":
        query = params["asset_query"]
        assets = await api.search_assets(query)
        if not assets:
            return await formatter.format_response("no_match", query, raw_text)
        asset_id = assets[0].get("asset_id") or assets[0].get("id")
        if not asset_id:
            return await formatter.format_response("no_match", query, raw_text)
        data = await api.post_cascade(
            asset_id,
            damage_level=params.get("damage_level", "destroyed"),
        )
        return await formatter.format_response("cascade", data, raw_text)

    return await formatter.format_response("help", None, raw_text)


@protocol.on_message(ChatMessage)
async def handle_message(ctx: Context, sender: str, msg: ChatMessage) -> None:
    await ctx.send(
        sender,
        ChatAcknowledgement(
            timestamp=datetime.now(),
            acknowledged_msg_id=msg.msg_id,
        ),
    )

    if EYE1_AGENT_ADDRESS:
        await ctx.send(EYE1_AGENT_ADDRESS, Eye1Query(query_type="ping"))
        ctx.logger.info("Pinged Eye 1 for asset index status")

    if EYE2_AGENT_ADDRESS:
        await ctx.send(EYE2_AGENT_ADDRESS, Eye2Query(query_type="ping"))
        ctx.logger.info("Pinged Eye 2 for heuristic status")

    if EYE3_AGENT_ADDRESS:
        await ctx.send(EYE3_AGENT_ADDRESS, Eye3Query(query_type="ping"))
        ctx.logger.info("Pinged Eye 3 for cascade status")

    text = _extract_text(msg)
    if not text:
        reply = await formatter.format_response("help", None, "")
    else:
        try:
            routed = await router.route(text)
            ctx.logger.info(
                f"Routed to {routed.intent} with params {routed.params}"
            )
            cascade_context = _build_cascade_context()
            ctx.logger.info(f"Cascade context assembled from {len(cascade_context.contributing_agents)} specialist agents: {cascade_context.contributing_agents}")
            reply = await _dispatch(routed, text)
        except TPIError as exc:
            ctx.logger.exception("TriNetra API error")
            reply = await formatter.format_response("error", str(exc), text)
        except Exception as exc:
            ctx.logger.exception("Unhandled error in message handler")
            reply = await formatter.format_response("error", str(exc), text)

    reply = "🛰️ Eye 1 coordinating · Eye 2 coordinating · Eye 3 coordinating\n\n" + reply
    await ctx.send(
        sender,
        ChatMessage(
            timestamp=datetime.utcnow(),
            msg_id=uuid4(),
            content=[
                TextContent(type="text", text=reply),
                EndSessionContent(type="end-session"),
            ],
        ),
    )


@protocol.on_message(ChatAcknowledgement)
async def handle_ack(ctx: Context, sender: str, msg: ChatAcknowledgement) -> None:
    pass


@agent.on_message(model=Eye1Response)
async def handle_eye1_response(ctx: Context, sender: str, msg: Eye1Response) -> None:
    ctx.logger.info(f"Eye 1 reported: {msg.message} | data={msg.data}")
    _eye_state["eye1"] = msg.data


@agent.on_message(model=Eye2Response)
async def handle_eye2_response(ctx: Context, sender: str, msg: Eye2Response) -> None:
    ctx.logger.info(f"Eye 2 reported: {msg.message} | data={msg.data}")
    _eye_state["eye2"] = msg.data


@agent.on_message(model=Eye3Response)
async def handle_eye3_response(ctx: Context, sender: str, msg: Eye3Response) -> None:
    ctx.logger.info(f"Eye 3 reported: {msg.message} | data={msg.data}")
    _eye_state["eye3"] = msg.data


def _build_cascade_context() -> CascadeContext:
    """Aggregate latest specialist agent state into a unified cascade context."""
    eye1 = _eye_state.get("eye1") or {}
    eye2 = _eye_state.get("eye2") or {}
    eye3 = _eye_state.get("eye3") or {}
    contributing = [k for k, v in _eye_state.items() if v is not None]
    return CascadeContext(
        asset_index_size=eye1.get("asset_count"),
        disaster_profile=eye2 if eye2 else None,
        orchestration_status=eye3.get("status") if eye3 else None,
        contributing_agents=contributing,
    )


agent.include(protocol, publish_manifest=True)


if __name__ == "__main__":
    agent.run()
