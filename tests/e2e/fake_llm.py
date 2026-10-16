"""A fake Anthropic Messages API for the e2e demo. No real AI, no network, no API key.

Messaging's agent talks to this because ANTHROPIC_BASE_URL points here. It answers like
Claude would: for a request it picks a tool (tool_use); when the tool result comes back it
writes a short reply (end_turn). The agent code, tool checks and Money calls are all real.
"""

import json

from fastapi import FastAPI

app = FastAPI(title="Fake LLM")

# What the student says (lowercase keyword) -> the tool call a real model would make
TOOL_CALLS = [
    (
        "auto-convert",
        "propose_rule",
        {
            "from_currency": "BDT",
            "to_currency": "IDR",
            "target_rate": "136",
            "amount_per_run": "500",
            "max_per_month": "2000",
            "valid_days": 30,
        },
    ),
    (
        "convert",
        "propose_transfer",
        {"from_currency": "BDT", "to_currency": "IDR", "amount": "1000"},
    ),
    ("rate", "get_rate", {"pair": "BDT_IDR"}),
]


@app.post("/v1/messages")
async def messages(body: dict) -> dict:
    last = body["messages"][-1]["content"]
    if isinstance(last, list):  # the agent sent back tool results
        return _answer(body, "end_turn", [{"type": "text", "text": _reply_for(last[0])}])

    text = last.lower()
    for keyword, tool, tool_input in TOOL_CALLS:
        if keyword in text:
            block = {"type": "tool_use", "id": "toolu_fake1", "name": tool, "input": tool_input}
            return _answer(body, "tool_use", [block])
    return _answer(body, "end_turn", [{"type": "text", "text": "Hi! Ask me about rates."}])


def _reply_for(tool_result: dict) -> str:
    if tool_result.get("is_error"):
        return "Sorry, that didn't work. Please try again."
    result = json.loads(tool_result["content"])
    if "rate" in result:
        return f"1 BDT = {result['rate']} IDR right now."
    if result.get("status") == "pending_approval":
        return "Done. Please check the approval link I sent you."
    return f"I couldn't do that: {result.get('reason')}"


def _answer(body: dict, stop_reason: str, content: list) -> dict:
    return {
        "id": "msg_fake",
        "type": "message",
        "role": "assistant",
        "model": body["model"],
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }
