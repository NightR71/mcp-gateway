"""M5 真模型接入排障脚本：直接打 OpenAI 兼容端点，打印**真实**的错误与响应体。

为什么需要它
------------
网关对外只返回 `服务内部错误（关联ID: …）`（M1 §5.3 错误脱敏，这是对的），而
`log_and_hide` 记的是 `str(exc)`——对 `httpx.HTTPStatusError` 而言这个字符串**不含
响应体**，所以连服务端日志也看不到提供方真正返回了什么（例如 `Insufficient Balance`）。
排障时需要一个能拿到原始状态码 + 响应体的工具，于是有了本脚本。

安全
----
- Key 只从环境变量 `GATEWAY_AGENT_API_KEY` 读，**永不打印**（含错误分支：只打印状态码、
  响应体与异常类型/文本，httpx 的异常文本不含请求头）。
- 不发任何真实业务问题，用一个固定的短探针问题，成本可忽略。

用法（在设了 Key 的那个终端里跑）
--------------------------------
    uv run python scripts/m5_diag_model.py                 # 默认连测 5 次，流式 + 非流式
    uv run python scripts/m5_diag_model.py --times 10      # 多测几轮，看失败率
    uv run python scripts/m5_diag_model.py --mode stream   # 只测流式（网关走的就是这条）
    uv run python scripts/m5_diag_model.py --times 10 --mode stream
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import httpx
import yaml

BASE_DIR = Path(__file__).resolve().parents[1]
REAL_CONFIG = BASE_DIR / "config" / "gateway.real.yaml"

# 固定探针问题（短、无隐私内容、会触发一次工具调用以复现网关真实调用形态）
PROBE_QUESTION = "请用一句话介绍这个网关"

TOOL_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "resume_kb__search_knowledge",
            "description": "检索简历知识库",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    }
]


def load_agent_config() -> tuple[str, str]:
    """从 gateway.real.yaml 取 base_url/model（缺省回退 DeepSeek 官方端点）。"""
    config = yaml.safe_load(REAL_CONFIG.read_text(encoding="utf-8")) or {}
    agent = config.get("agent") or {}
    return (
        str(agent.get("base_url") or "https://api.deepseek.com/v1"),
        str(agent.get("model") or "deepseek-flash"),
    )


def describe_error(exc: Exception) -> str:
    """把异常压成一行诊断文本（只含类型与文本，不含任何请求头/凭据）。"""
    return f"{type(exc).__name__}: {exc}"


async def probe_once(
    client: httpx.AsyncClient, url: str, api_key: str, model: str, *, stream: bool
) -> tuple[str, str]:
    """打一次端点，返回 (结果标签, 细节文本)。"""
    body: dict[str, object] = {
        "model": model,
        "messages": [{"role": "user", "content": PROBE_QUESTION}],
        "tools": TOOL_SCHEMA,
        "temperature": 0,
    }
    if stream:
        body["stream"] = True
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        if not stream:
            resp = await client.post(url, headers=headers, json=body)
            if resp.status_code != 200:
                return (
                    f"HTTP {resp.status_code}",
                    resp.text[:400].replace("\n", " "),
                )
            payload = resp.json()
            choice = (payload.get("choices") or [{}])[0]
            usage = payload.get("usage") or {}
            return (
                "OK",
                f"finish={choice.get('finish_reason')} "
                f"tool_calls={len((choice.get('message') or {}).get('tool_calls') or [])} "
                f"tokens={usage.get('total_tokens')}",
            )

        async with client.stream("POST", url, headers=headers, json=body) as resp:
            if resp.status_code != 200:
                raw = (await resp.aread()).decode("utf-8", "replace")
                return (f"HTTP {resp.status_code}", raw[:400].replace("\n", " "))
            chunks = 0
            done = False
            async for line in resp.aiter_lines():
                if line.startswith("data:"):
                    chunks += 1
                    if line[5:].strip() == "[DONE]":
                        done = True
                        break
            return ("OK", f"chunks={chunks} [DONE]={done}")
    except Exception as exc:  # 排障脚本：任何异常都要如实打印类型与文本
        return ("EXC", describe_error(exc))


async def main() -> int:
    parser = argparse.ArgumentParser(description="M5 真模型接入排障（打印原始状态码与响应体）")
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--times", type=int, default=5)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--mode", choices=["both", "stream", "plain"], default="both")
    args = parser.parse_args()

    configured_url, configured_model = load_agent_config()
    base_url = args.base_url or configured_url
    model = args.model or configured_model
    url = f"{base_url.rstrip('/')}/chat/completions"

    api_key = os.getenv("GATEWAY_AGENT_API_KEY", "")
    if not api_key:
        print(
            "未检测到环境变量 GATEWAY_AGENT_API_KEY。\n"
            "请在本机**设了 Key 的那个终端**里运行本脚本（Key 只从环境变量读取，不会被打印）。",
            file=sys.stderr,
        )
        return 2

    modes = ["plain", "stream"] if args.mode == "both" else [args.mode]
    print(f"[diag] 端点 = {url}")
    print(f"[diag] 模型 = {model}")
    print(f"[diag] Key   = 已从环境变量读取（长度 {len(api_key)}，不打印内容）")
    print(f"[diag] 轮次 = {args.times} × {len(modes)} 种模式，间隔 {args.interval}s\n")

    summary: dict[str, int] = {}
    async with httpx.AsyncClient(timeout=120.0) as client:
        for index in range(1, args.times + 1):
            for mode in modes:
                label, detail = await probe_once(
                    client, url, api_key, model, stream=(mode == "stream")
                )
                summary[f"{mode}:{label}"] = summary.get(f"{mode}:{label}", 0) + 1
                print(f"  [{index:>2}/{args.times}] {mode:<6} {label:<12} {detail[:160]}")
                await asyncio.sleep(args.interval)

    print("\n[diag] 汇总：")
    for key, count in sorted(summary.items()):
        print(f"  {key:<24} {count}")
    failures = sum(count for key, count in summary.items() if not key.endswith(":OK"))
    print(f"\n[diag] 失败 {failures} / {args.times * len(modes)} 次")
    if failures:
        print(
            "[diag] 判读：\n"
            "  - HTTP 4xx/5xx + 响应体 → 提供方侧拒绝（看 body 里的 message，如余额/限流/模型名）\n"
            "  - EXC + ConnectError/RemoteProtocolError → 连接层（网关层可加重试）\n"
            "  - EXC + ReadTimeout → 提供方响应过慢（需评估超时与降级）\n"
            "  - EXC + JSONDecodeError → 流式分块解析不够健壮（网关层需修）"
        )
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
