# Orderflow Research Desk · 订单流纸面研究台

> **⚠️ 纸面研究工具，不下单，不构成投资建议，不承诺收益。**
>
> **本仓库没有任何交易所下单接口。指标只标出结构和价位，所有记录都是纸面账，用来研究和复盘。**

一套免费的订单流研究流程：TradingView 指标负责看结构，发现「空头在关键区域砸了两次都没砸穿」时打包成 JSON，推给你自己的模型（Fable 5.2、Grok Bot 或任何 OpenAI 兼容接口），模型只回答三个词：**观察 / 再等 / 跳过**。

## 仓库里有什么

| 文件 | 用途 |
| --- | --- |
| `orderflow_research.pine` | TradingView 指标（Pine Script v5），5 分钟图用 |
| `prompts/research-prompt.md` | 给模型的研究提示词 |
| `server/app.py` | 接 TradingView 警报的小服务，记纸面账，可选转给模型 |
| `server/sample_alert.json` | 一条示例警报，本地测试用 |

## 9 步上手

**1. 把指标放上图表**
打开 TradingView，点底部的 Pine Editor，把 `orderflow_research.pine` 全部内容贴进去，点「添加到图表」，然后把图表切到 5 分钟。

**2. 先看大方向**
图表最右边有一个状态标签，读的是 1 小时和 4 小时已经收盘的 K 线：
- `VALUE UP`：两个周期都向上，只研究多头结构
- `VALUE DOWN`：两个周期都向下，只研究空头结构
- `BALANCE`：方向不一致，今天先观望

**3. 看灰色阴影（成交密集区）**
阴影是当天的 VWAP 上下一个标准差，可以理解成今天成交最集中的地方。多头结构只在阴影下方找，空头结构只在阴影上方找。价格在阴影里面、走到区间中间，就先记一笔「观望」。

**4. 看绿框**
指标会自动找最近一段波段，画出 0.705 到 0.886 的回撤区，就是那个绿色方框。只关心价格回到框里之后发生了什么。

**5. 等空头第二次失败**
框里出现放量大阴线（或者长下影），收盘却没跌破前一根低点，算空头失败一次，图上标「失败 1」。先别动，等第二次。第二次失败出现后，指标会画出蓝色观察位、红色止损位、绿色目标位。把这三个价格记到纸面上，不要下单。空头那边完全对称。

**6. 守纸面规则**
一天最多记 2 笔；当天纸面亏满 2 笔就停记。指标会自己跟踪止损和目标，到了会标「纸面：止损」或「纸面：到目标」，超出规则的结构会标「结构出现，不记」。

**7. 把警报接到你自己的研究台**
在 `server/` 目录启动服务（可以直接让 Claude Code 或 Fable 帮你跑这几行）：

```bash
cd server
pip install -r requirements.txt
python app.py            # 默认监听 127.0.0.1:8000
```

TradingView 只能推到公网地址，用 cloudflared 开一个临时隧道：

```bash
cloudflared tunnel --url http://localhost:8000
```

它会给你一个 `https://xxxx.trycloudflare.com` 地址。回到 TradingView，在这个指标上新建警报，条件选 **「any alert() function call」**，通知里勾上 Webhook URL，填 `https://xxxx.trycloudflare.com/webhook`。之后每条结构都会追加到 `server/ledger.csv`。

> TradingView 的 Webhook 警报需要付费账户并开启两步验证。想给地址加个口令，就设置环境变量 `WEBHOOK_TOKEN=你的口令`，URL 改成 `/webhook?token=你的口令`。

**8. 载入研究提示词**
把 `prompts/research-prompt.md` 里的「系统提示词」贴给 Fable 5.2 或 Grok Bot。想让服务自动转给模型，就在启动前设置这三个环境变量（参考 `.env.example`，不要把密钥提交到仓库）：

```bash
export LLM_BASE_URL=https://你的接口地址/v1
export LLM_API_KEY=你的密钥
export LLM_MODEL=模型名
python app.py
```

每条结构进来，模型的回复会写进 `server/replies.log`：只有「观察 / 再等 / 跳过」、纸面仓位假设，以及这套结构最可能在哪翻车。

**9. 先纸面记满一周**
至少记一整周再回头看 `ledger.csv`：哪些结构到了目标，哪些打了止损，模型说「跳过」的后来怎么走。这一周里不要下单。

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
  "event": "setup",
  "symbol": "ETHUSDT",
  "tf": "5",
  "bias": "VALUE UP",
  "location": "BELOW_VALUE",
  "side": "long",
  "zone": {"top": 3120.45, "bottom": 3098.1, "fib": "0.705-0.886"},
  "structure": "SELLERS_FAILED_TWICE",
  "watch": 3104.2,
  "stop": 3095.6,
  "target": 3121.4,
  "result": "open",
  "paper_today": {"records": 1, "losses": 0},
  "ts": 1794836700000
}
```

纸面记录结算时会再推一条 `"event": "paper_result"`，`result` 是 `target` 或 `stop`。

## 说明

- 大方向只读已收盘的 1 小时 / 4 小时 K 线，不重绘；结构在 5 分钟 K 线收盘后才确认。
- 指标里的纸面结算是简化版：假设在观察位记账，同一根 K 线同时碰到止损和目标按止损算，不计手续费和滑点。
- 参数（EMA 长度、放量倍数、风险回报倍数等）都在指标设置里，可以自己调。
- 过去的结构不代表以后，纸面结果也不代表实盘。

## License

MIT © 2026 Allencake
