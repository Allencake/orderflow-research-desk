"""Orderflow Research Desk —— 纸面研究 webhook。

接收 TradingView 警报推来的 JSON，追加到 ledger.csv；
如果配置了 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL，就把 JSON 连同研究提示词
发给一个 OpenAI 兼容接口，把回复写进 replies.log。

只记账、只研究，不下单，也没有任何交易所下单接口。
"""

from __future__ import annotations

import csv
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

HERE = Path(__file__).resolve().parent
LEDGER = Path(os.getenv("LEDGER_PATH", HERE / "ledger.csv"))
REPLIES = Path(os.getenv("REPLIES_PATH", HERE / "replies.log"))
PROMPT_PATH = Path(os.getenv("PROMPT_PATH", HERE.parent / "prompts" / "research-prompt.md"))

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "").rstrip("/")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "")
WEBHOOK_TOKEN = os.getenv("WEBHOOK_TOKEN", "")

FIELDS = [
    "received_at", "event", "symbol", "exchange", "tf", "bias", "location", "side",
    "zone_top", "zone_bottom", "structure", "watch", "stop", "target", "result",
    "records_today", "losses_today", "ts", "bar_time_utc",
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("orderflow-desk")

app = FastAPI(title="Orderflow Research Desk (paper only)")


def load_prompt() -> str:
    """取 research-prompt.md 里「系统提示词」那一节之后的内容；找不到就用整篇。"""
    text = PROMPT_PATH.read_text(encoding="utf-8")
    marker = "## 系统提示词"
    return text.split(marker, 1)[1].strip() if marker in text else text


def to_row(data: dict[str, Any]) -> dict[str, Any]:
    zone = data.get("zone") or {}
    today = data.get("paper_today") or {}
    ts = data.get("ts")
    bar_time = ""
    if isinstance(ts, (int, float)):
        bar_time = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat()
    return {
        "received_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "event": data.get("event", ""),
        "symbol": data.get("symbol", ""),
        "exchange": data.get("exchange", ""),
        "tf": data.get("tf", ""),
        "bias": data.get("bias", ""),
        "location": data.get("location", ""),
        "side": data.get("side", ""),
        "zone_top": zone.get("top", "") if isinstance(zone, dict) else "",
        "zone_bottom": zone.get("bottom", "") if isinstance(zone, dict) else "",
        "structure": data.get("structure", ""),
        "watch": data.get("watch", ""),
        "stop": data.get("stop", ""),
        "target": data.get("target", ""),
        "result": data.get("result", ""),
        "records_today": today.get("records", "") if isinstance(today, dict) else "",
        "losses_today": today.get("losses", "") if isinstance(today, dict) else "",
        "ts": ts if ts is not None else "",
        "bar_time_utc": bar_time,
    }


def append_ledger(row: dict[str, Any]) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    new_file = not LEDGER.exists() or LEDGER.stat().st_size == 0
    with LEDGER.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if new_file:
            writer.writeheader()
        writer.writerow(row)


def llm_enabled() -> bool:
    return bool(LLM_BASE_URL and LLM_API_KEY and LLM_MODEL)


def ask_llm(data: dict[str, Any]) -> None:
    """把 JSON 发给 OpenAI 兼容接口，回复写进 replies.log。失败只记日志，不影响记账。"""
    try:
        resp = httpx.post(
            f"{LLM_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {LLM_API_KEY}"},
            json={
                "model": LLM_MODEL,
                "temperature": 0.2,
                "messages": [
                    {"role": "system", "content": load_prompt()},
                    {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
                ],
            },
            timeout=60,
        )
        resp.raise_for_status()
        reply = resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as exc:  # noqa: BLE001 —— 研究助手挂了也要继续记账
        reply = f"[LLM 调用失败] {exc}"
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    head = f"{data.get('symbol', '')} {data.get('side', '')} {data.get('event', '')}"
    with REPLIES.open("a", encoding="utf-8") as f:
        f.write(f"=== {stamp} {head}\n{reply}\n\n")
    log.info("研究回复 %s:\n%s", head, reply)


@app.get("/")
def health() -> dict[str, Any]:
    return {"ok": True, "paper_only": True, "llm": llm_enabled(), "ledger": str(LEDGER)}


@app.post("/webhook")
async def webhook(request: Request, background: BackgroundTasks, token: str = "") -> dict[str, Any]:
    if WEBHOOK_TOKEN and token != WEBHOOK_TOKEN:
        raise HTTPException(status_code=401, detail="bad token")
    raw = await request.body()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(status_code=400, detail="body must be JSON")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="JSON must be an object")

    append_ledger(to_row(data))
    log.info("记账 %s %s %s watch=%s stop=%s target=%s", data.get("event"), data.get("symbol"),
             data.get("side"), data.get("watch"), data.get("stop"), data.get("target"))

    if llm_enabled():
        background.add_task(ask_llm, data)
    return {"ok": True, "recorded": True, "llm": llm_enabled()}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8000")))
