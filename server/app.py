"""Orderflow Research Desk —— TradingView webhook 服务。

1. 接收 TradingView 警报推来的 JSON，追加到 ledger.csv
2. 配置了 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL 时，把 JSON 连同交易提示词
   发给一个 OpenAI 兼容接口，回复写进 replies.log，结论写进 decisions.csv
3. 结论是 TAKE，且配置了 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 时，用 Telegram 机器人推送到手机

只做记录、判断和通知，不连接任何交易所，不会自动下单。
仅供学习交流，不构成投资建议，交易有风险。
"""

from __future__ import annotations

import csv
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

HERE = Path(__file__).resolve().parent
LEDGER = Path(os.getenv("LEDGER_PATH", HERE / "ledger.csv"))
DECISIONS = Path(os.getenv("DECISIONS_PATH", HERE / "decisions.csv"))
REPLIES = Path(os.getenv("REPLIES_PATH", HERE / "replies.log"))
PROMPT_PATH = Path(os.getenv("PROMPT_PATH", HERE.parent / "prompts" / "trade-prompt.md"))

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "").rstrip("/")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_API_BASE = os.getenv("TELEGRAM_API_BASE", "https://api.telegram.org").rstrip("/")
WEBHOOK_TOKEN = os.getenv("WEBHOOK_TOKEN", "")

LEDGER_FIELDS = [
    "received_at", "event", "symbol", "exchange", "tf", "bias", "location", "side",
    "zone_top", "zone_bottom", "structure", "entry", "stop", "target", "rr", "result",
    "reason", "exit", "r", "trades_today", "losses_today", "ts", "bar_time_utc",
]
DECISION_FIELDS = ["decided_at", "symbol", "side", "entry", "stop", "target", "decision", "telegram", "ts"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("orderflow-desk")

app = FastAPI(title="Orderflow Research Desk")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def append_csv(path: Path, fields: list[str], row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if new_file:
            writer.writeheader()
        writer.writerow(row)


def load_prompt() -> str:
    """取 trade-prompt.md 里「系统提示词」那一节之后的内容；找不到就用整篇。"""
    text = PROMPT_PATH.read_text(encoding="utf-8")
    marker = "## 系统提示词"
    return text.split(marker, 1)[1].strip() if marker in text else text


def to_row(data: dict[str, Any]) -> dict[str, Any]:
    zone = data.get("zone") if isinstance(data.get("zone"), dict) else {}
    today = data.get("today") if isinstance(data.get("today"), dict) else {}
    ts = data.get("ts")
    bar_time = ""
    if isinstance(ts, (int, float)):
        bar_time = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat()
    row = {k: data.get(k, "") for k in LEDGER_FIELDS}
    row.update({
        "received_at": now(),
        "zone_top": zone.get("top", ""),
        "zone_bottom": zone.get("bottom", ""),
        "trades_today": today.get("trades", ""),
        "losses_today": today.get("losses", ""),
        "ts": ts if ts is not None else "",
        "bar_time_utc": bar_time,
    })
    return row


def llm_enabled() -> bool:
    return bool(LLM_BASE_URL and LLM_API_KEY and LLM_MODEL)


def telegram_enabled() -> bool:
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def parse_decision(reply: str) -> str:
    """回复第一行应该是 TAKE / WAIT / SKIP；认不出来就当 SKIP。"""
    first = reply.strip().splitlines()[0] if reply.strip() else ""
    m = re.search(r"\b(TAKE|WAIT|SKIP)\b", first.upper())
    return m.group(1) if m else "SKIP"


def send_telegram(data: dict[str, Any], reply: str) -> bool:
    text = (
        f"🚨 TAKE · {data.get('symbol', '')} {data.get('side', '')} ({data.get('tf', '')}m)\n"
        f"入场 {data.get('entry')} · 止损 {data.get('stop')} · 目标 {data.get('target')}\n"
        f"大方向 {data.get('bias', '')} · {data.get('structure', '')}\n\n"
        f"{reply.strip()}"
    )
    try:
        resp = httpx.post(
            f"{TELEGRAM_API_BASE}/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text[:4000], "disable_web_page_preview": True},
            timeout=15,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001 —— 通知失败不影响记账
        log.warning("Telegram 推送失败：%s", type(exc).__name__)
        return False


def ask_llm(data: dict[str, Any]) -> None:
    """把信号发给模型；结论是 TAKE 就推 Telegram。任何一步失败都只记日志。"""
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
        decision = parse_decision(reply)
    except Exception as exc:  # noqa: BLE001
        reply, decision = f"[LLM 调用失败] {type(exc).__name__}: {exc}", "ERROR"

    head = f"{data.get('symbol', '')} {data.get('side', '')} {data.get('event', '')}"
    with REPLIES.open("a", encoding="utf-8") as f:
        f.write(f"=== {now()} {head} -> {decision}\n{reply}\n\n")
    log.info("模型结论 %s -> %s", head, decision)

    if data.get("event") != "signal":
        return
    sent = decision == "TAKE" and telegram_enabled() and send_telegram(data, reply)
    append_csv(DECISIONS, DECISION_FIELDS, {
        "decided_at": now(), "symbol": data.get("symbol", ""), "side": data.get("side", ""),
        "entry": data.get("entry", ""), "stop": data.get("stop", ""), "target": data.get("target", ""),
        "decision": decision, "telegram": "sent" if sent else "", "ts": data.get("ts", ""),
    })


@app.get("/")
def health() -> dict[str, Any]:
    return {"ok": True, "llm": llm_enabled(), "telegram": telegram_enabled(), "ledger": str(LEDGER)}


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

    append_csv(LEDGER, LEDGER_FIELDS, to_row(data))
    log.info("记录 %s %s %s entry=%s stop=%s target=%s %s", data.get("event"), data.get("symbol"),
             data.get("side"), data.get("entry"), data.get("stop"), data.get("target"), data.get("result"))

    # BLOCKED 的信号（当天已做满或已亏满）只记账，不问模型
    if llm_enabled() and data.get("result") != "BLOCKED":
        background.add_task(ask_llm, data)
    return {"ok": True, "recorded": True, "llm": llm_enabled(), "telegram": telegram_enabled()}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8000")))
