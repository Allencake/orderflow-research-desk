# Orderflow Research Desk · 订单流交易指标

> 仅供学习交流，不构成投资建议，交易有风险。

把「空头在关键位置砸了两次都没砸穿」这套订单流打法写成一个免费的 TradingView 指标：结构一出现就打出 **LONG / SHORT**，给出入场、止损、目标，并把整笔交易打包成 JSON 推给你的 AI（Fable 5.2、Grok Bot 或任何 OpenAI 兼容接口）。AI 回答 **TAKE / WAIT / SKIP**，附上仓位建议、理由和主要风险；结论是 TAKE 时，Telegram 机器人直接推到你手机上。

指标只出信号和警报，不连接交易所，不会替你自动下单。

## 仓库里有什么

| 文件 | 用途 |
| --- | --- |
| `orderflow_research.pine` | TradingView 指标（Pine Script v5），5 分钟图用 |
| `prompts/trade-prompt.md` | 给 AI 的交易提示词 |
| `server/app.py` | 接 TradingView 警报的小服务：记账、问 AI、TAKE 时推 Telegram |
| `server/sample_alert.json` | 一条示例信号，本地测试用 |

## 9 步上手

**1. 把指标放上图表**
打开 TradingView，点底部的 Pine Editor，把 `orderflow_research.pine` 全部内容贴进去，点「添加到图表」，图表切到 5 分钟。

**2. 先看大方向**
图表最右边的状态标签读的是 1 小时和 4 小时已经收盘的 K 线：
- `VALUE UP`：只找多单
- `VALUE DOWN`：只找空单
- `BALANCE`：今天不交易

**3. 看灰色阴影（成交密集区）**
阴影是当天的 VWAP 上下一个标准差，也就是今天成交最集中的地方。多单只在阴影下方做，空单只在阴影上方做，价格在区间中间就不做。

**4. 看绿框**
指标自动找最近一段波段，画出 0.705 到 0.886 的斐波那契区域，就是那个绿色方框。框里的每根 K 线都会被「透视」：放量大阴线或长下影、收盘却没跌破前一根低点，就标「失败 1」，意思是空头砸得很凶但没砸下去。

**5. 等空头第二次失败**
第一次失败先别动。空头第二次再砸、再失败，这时他们被套住了，指标会打出 `LONG` 标签，同时画出蓝色入场、红色止损、绿色目标。空单完全对称，打 `SHORT`。

**6. 守交易规则**
一天最多 2 笔；当天亏满 2 笔就停手，后面的信号会标「不做」。持仓浮盈到 1 倍风险后，橙色的移动止损线会自动跟上，至少保本。出场时会标「到目标 / 移动止损出场 / 保本出场 / 止损」和这笔赚了几个 R。

**7. 让 Claude Code 里的 Fable 5.2 把仓库跑起来**
直接对 Fable 5.2 说「把这个仓库的 server 跑起来，给我一个公网 webhook 地址」，或者自己跑：

```bash
cd server
pip install -r requirements.txt
python app.py                                # 默认监听 127.0.0.1:8000
cloudflared tunnel --url http://localhost:8000   # 另开一个终端，拿到 https://xxxx.trycloudflare.com
```

回到 TradingView，在这个指标上新建**一条**警报，条件选 **「any alert() function call」**，通知里勾上 Webhook URL，填 `https://xxxx.trycloudflare.com/webhook`。之后每个信号都会追加到 `server/ledger.csv`。

> TradingView 的 Webhook 警报需要付费账户并开启两步验证。想给地址加个口令，设置 `WEBHOOK_TOKEN=你的口令`，URL 改成 `/webhook?token=你的口令`。

**8. 载入交易提示词**
`prompts/trade-prompt.md` 就是给 AI 的提示词。让服务自动把每个信号转给 AI，启动前设置这三个环境变量（参考 `.env.example`，密钥别提交进仓库）：

```bash
export LLM_BASE_URL=https://你的接口地址/v1
export LLM_API_KEY=你的密钥
export LLM_MODEL=模型名
```

每个信号进来，AI 会回 `TAKE`、`WAIT` 或 `SKIP`，按单笔风险 1% 算好仓位占账户的比例，再写理由和这笔最可能在哪失败。回复都在 `server/replies.log`，结论汇总在 `server/decisions.csv`。

**9. 接上 Telegram，TAKE 就推到手机**
建议先拿模拟盘跑一周，看看 `ledger.csv` 和 `decisions.csv` 里信号的表现。然后在 Telegram 里找 @BotFather 建一个机器人，拿到 token；给机器人发一句话，再拿到你的 chat id。设置：

```bash
export TELEGRAM_BOT_TOKEN=你的机器人token
export TELEGRAM_CHAT_ID=你的chat_id
python app.py
```

从此空头第二次被套、AI 说 TAKE 的那一刻，手机就会响，消息里带入场、止损、目标和 AI 的仓位建议。

## 本地测试

```bash
cd server
python app.py &
curl -X POST http://127.0.0.1:8000/webhook \
  -H "Content-Type: application/json" \
  --data @sample_alert.json
# 返回 {"ok":true,"recorded":true,...}，并生成 ledger.csv
```

## 警报 JSON 长什么样

```json
{
  "event": "signal",
  "symbol": "ETHUSDT",
  "tf": "5",
  "bias": "VALUE UP",
  "location": "BELOW_VALUE",
  "side": "LONG",
  "zone": {"top": 3120.45, "bottom": 3098.1, "fib": "0.705-0.886"},
  "structure": "SELLERS_TRAPPED_TWICE",
  "entry": 3104.2,
  "stop": 3095.6,
  "target": 3121.4,
  "rr": 2,
  "result": "OPEN",
  "today": {"trades": 1, "losses": 0, "max_trades": 2, "max_losses": 2},
  "ts": 1794836700000
}
```

- `result` 为 `BLOCKED` 时，说明当天已做满 2 笔或已亏满 2 笔，`reason` 写了原因。服务只记账，不再问 AI。
- 出场时会再推一条 `"event": "exit"`，带 `exit`（出场价）、`r`（赚了几个 R）和 `result`（`TARGET` / `TRAIL_STOP_WIN` / `BREAKEVEN` / `STOP_LOSS`）。

## 说明

- 大方向只读已收盘的 1 小时 / 4 小时 K 线，不重绘；信号在 5 分钟 K 线收盘后才确认。
- 指标里的出场统计是简化版：按信号 K 线收盘价入场，同一根 K 线同时碰到止损和目标按止损算，不计手续费和滑点。
- 参数（EMA 长度、放量倍数、风险回报、移动止损距离等）都在指标设置里，可以自己调。
- 历史表现不代表未来。

## License

MIT © 2026 Allencake
