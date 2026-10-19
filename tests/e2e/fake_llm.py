"""A fake AI API for the e2e demo. No real AI, no network, no API key.

It speaks both providers' formats: Gemini (POST /v1beta/models/<model>:generateContent,
used when GEMINI_BASE_URL points here) and Anthropic (POST /v1/messages, used when
ANTHROPIC_BASE_URL points here). For a request it picks a tool; when the tool result comes
back it writes a short reply. The agent code, tool checks and Money calls are all real.
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
GREETING = "Hi! Ask me about rates."


def _pick_tool(text: str) -> tuple[str, dict] | None:
    for keyword, tool, tool_input in TOOL_CALLS:
        if keyword in text.lower():
            return tool, tool_input
    return None


def _reply_for(result: dict | None, error: str | None) -> str:
    if error:
        return "Sorry, that didn't work. Please try again."
    if "rate" in result:
        return f"1 BDT = {result['rate']} IDR right now."
    if result.get("status") == "pending_approval":
        return "Done. Please check the approval link I sent you."
    return f"I couldn't do that: {result.get('reason')}"


# --- Gemini format ---


@app.post("/v1beta/models/{model}:generateContent")
async def gemini(model: str, body: dict) -> dict:
    parts = body["contents"][-1]["parts"]
    if "functionResponse" in parts[0]:  # the agent sent back tool results
        response = parts[0]["functionResponse"]["response"]
        reply = _reply_for(response.get("result"), response.get("error"))
        return _gemini_answer([{"text": reply}])

    picked = _pick_tool(parts[0]["text"])
    if picked:
        call = {"id": "call_fake1", "name": picked[0], "args": picked[1]}
        return _gemini_answer([{"functionCall": call, "thoughtSignature": "fake-sig"}])
    return _gemini_answer([{"text": GREETING}])


def _gemini_answer(parts: list) -> dict:
    content = {"role": "model", "parts": parts}
    return {"candidates": [{"content": content, "finishReason": "STOP"}]}


# --- Anthropic format ---


@app.post("/v1/messages")
async def messages(body: dict) -> dict:
    last = body["messages"][-1]["content"]
    if isinstance(last, list):  # the agent sent back tool results
        result = last[0]
        if result.get("is_error"):
            reply = _reply_for(None, result["content"])
        else:
            reply = _reply_for(json.loads(result["content"]), None)
        return _anthropic_answer(body, "end_turn", [{"type": "text", "text": reply}])

    picked = _pick_tool(last)
    if picked:
        block = {"type": "tool_use", "id": "toolu_fake1", "name": picked[0], "input": picked[1]}
        return _anthropic_answer(body, "tool_use", [block])
    return _anthropic_answer(body, "end_turn", [{"type": "text", "text": GREETING}])


def _anthropic_answer(body: dict, stop_reason: str, content: list) -> dict:
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
