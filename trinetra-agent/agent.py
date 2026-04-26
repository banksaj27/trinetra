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

config.validate()

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

    text = _extract_text(msg)
    if not text:
        reply = await formatter.format_response("help", None, "")
    else:
        try:
            routed = await router.route(text)
            ctx.logger.info(
                f"Routed to {routed.intent} with params {routed.params}"
            )
            reply = await _dispatch(routed, text)
        except TPIError as exc:
            ctx.logger.exception("TriNetra API error")
            reply = await formatter.format_response("error", str(exc), text)
        except Exception as exc:
            ctx.logger.exception("Unhandled error in message handler")
            reply = await formatter.format_response("error", str(exc), text)

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


agent.include(protocol, publish_manifest=True)


if __name__ == "__main__":
    agent.run()
