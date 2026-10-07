"""Add a Claude-written daily briefing to site/data.json.

Skips cleanly (exit 0) when ANTHROPIC_API_KEY is unset, or when the existing
briefing is newer than BRIEFING_MAX_AGE_HOURS (default 11). Set
FORCE_BRIEFING=1 to regenerate regardless.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "site" / "data.json"
MODEL = "claude-opus-5-5"
HEADLINES_PER_TOPIC = 12

SYSTEM = """You write a concise morning-paper style briefing for one reader who \
follows US politics, the Middle East, stock markets, gold/commodities and AI.

You get today's top headlines per topic (with outlet names and short snippets) \
and current market prices. For each topic write 2-4 bullet points: what \
happened and why it matters. Merge duplicate stories, name the outlets that \
reported each point in parentheses, and use only facts present in the \
headlines - do not invent numbers. Then write a short "Watch next" list of \
2-4 things likely to move markets or the news in the coming days."""

SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string", "description": "One-sentence summary of the day"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "bullets": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["topic", "bullets"],
                "additionalProperties": False,
            },
        },
        "watch_next": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["headline", "sections", "watch_next"],
    "additionalProperties": False,
}


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def build_prompt(data: dict) -> str:
    lines = [f"Current time (UTC): {data['generated_at']}", "", "## Markets"]
    for m in data.get("markets", []):
        change = f"{m['change_pct']:+.2f}%" if m.get("change_pct") is not None else "n/a"
        lines.append(f"- {m['label']}: {m['price']:,.2f} ({change} vs prev close)")
    for topic in data["topics"]:
        lines += ["", f"## {topic['label']}"]
        for item in topic["items"][:HEADLINES_PER_TOPIC]:
            outlets = ", ".join([item["source"], *item.get("also", [])])
            snippet = f" — {item['summary']}" if item.get("summary") else ""
            lines.append(f"- [{outlets}] {item['title']}{snippet}")
    return "\n".join(lines)


def is_fresh(briefing: dict | None) -> bool:
    if not briefing or os.environ.get("FORCE_BRIEFING") == "1":
        return False
    max_age = float(os.environ.get("BRIEFING_MAX_AGE_HOURS", "11"))
    created = datetime.fromisoformat(briefing["generated_at"])
    return (datetime.now(timezone.utc) - created).total_seconds() / 3600 < max_age


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        log("ANTHROPIC_API_KEY not set; skipping briefing")
        return 0
    data = json.loads(DATA.read_text())
    if is_fresh(data.get("briefing")):
        log("Briefing is still fresh; skipping")
        return 0

    import anthropic

    client = anthropic.Anthropic()
    try:
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM,
            output_config={"effort": "low",
                           "format": {"type": "json_schema", "schema": SCHEMA}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": build_prompt(data)}],
        )
    except anthropic.APIStatusError as exc:
        log(f"Briefing request failed ({exc.status_code}): {exc.message}; keeping old briefing")
        return 0
    except anthropic.APIConnectionError as exc:
        log(f"Could not reach the API: {exc}; keeping old briefing")
        return 0

    if response.stop_reason != "end_turn":
        log(f"Briefing stopped early ({response.stop_reason}); keeping old briefing")
        return 0
    text = "".join(b.text for b in response.content if b.type == "text")
    briefing = json.loads(text)
    briefing["generated_at"] = datetime.now(timezone.utc).isoformat()
    briefing["model"] = response.model
    data["briefing"] = briefing
    DATA.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    log(f"Briefing written ({response.usage.input_tokens} in / "
        f"{response.usage.output_tokens} out tokens)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
