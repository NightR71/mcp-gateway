"""M6 模型名核对（M6 §2.5）：确认配置里的模型名在提供方侧是否仍然有效。

背景：M5 报告 §7.8 留了「按官方文档核对 deepseek-chat 是否仍是现役口径」这一待查项。
2026-09-18 核对官方文档（https://api-docs.deepseek.com/quick_start/pricing）时发现
文档列出的模型名已变成 `deepseek-flash` / `deepseek-v4-pro`，**不再出现 `deepseek-chat`**
（文档只说明 legacy 名 `deepseek-v4-flash` 仍被接受）。文档口径与线上实际可接受的名字
之间可能有差异，所以这里用**提供方自己返回的模型清单**来判定，而不是猜。

本脚本只做两件事，都不打印任何凭据：
1. `GET {base_url}/models`（**免费**）——列出账号可见的模型 id，并判断配置里的模型名是否在列；
2. `--chat`（可选，消耗 1 次极小调用）——用配置的模型名发一句最短请求，如实打印 HTTP 状态与
   错误体（提供方返回的报错是最权威的"这个名字还有没有效"的证据）。

用法（Key 只走环境变量，绝不写进命令行参数或文件）：

    # 只列模型清单（零成本）
    GATEWAY_AGENT_API_KEY=<你的 Key> uv run python scripts/m6_model_check.py

    # 再打一次最小 chat 调用验证该名字真的可用（约 1 个 token）
    GATEWAY_AGENT_API_KEY=<你的 Key> uv run python scripts/m6_model_check.py --chat

配置文件默认读 `config/gateway.real.yaml`（与线上 vercel 配置同口径），
可用 `--config config/gateway.vercel.yaml` 指定；也可用 `GATEWAY_CONFIG_FILE` 环境变量。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import httpx
import yaml

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = BASE_DIR / "config" / "gateway.real.yaml"
KEY_ENV = "GATEWAY_AGENT_API_KEY"
TIMEOUT = 30.0


def load_agent_config(path: Path) -> tuple[str, str]:
    """从配置文件的 agent 节读 (model, base_url)。"""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    agent = data.get("agent") or {}
    model = str(agent.get("model", ""))
    base_url = str(agent.get("base_url", "")).rstrip("/")
    if not model or not base_url:
        raise SystemExit(f"{path} 的 agent 节缺少 model / base_url")
    return model, base_url


def require_key() -> str:
    """取环境变量里的 Key；未设置时给出明确指引（不索要、不落盘）。"""
    key = os.getenv(KEY_ENV, "").strip()
    if not key:
        raise SystemExit(
            f"未设置环境变量 {KEY_ENV}。请在你自己的终端里设置后再运行本脚本：\n"
            f"  PowerShell:  $env:{KEY_ENV} = '<你的 Key>'\n"
            f"  Git Bash:    export {KEY_ENV}='<你的 Key>'\n"
            "（Key 不要发给我、不要写进任何文件；脚本不会打印它，也不会写日志。）"
        )
    return key


async def check(config_path: Path, do_chat: bool) -> int:
    model, base_url = load_agent_config(config_path)
    key = require_key()
    headers = {"Authorization": f"Bearer {key}"}
    print(f"配置：{config_path.name}")
    print(f"  base_url = {base_url}")
    print(f"  model    = {model}")
    print(f"  Key      = 已从 {KEY_ENV} 读取（长度 {len(key)}，内容不显示）\n")

    ok = True
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        # 1) 模型清单（免费）
        try:
            resp = await client.get(f"{base_url}/models", headers=headers)
        except Exception as exc:  # 网络/证书问题如实报出（M5 §7.2 同款环境问题）
            print(f"[1] GET /models 失败（网络层）：{type(exc).__name__}: {exc}")
            return 2
        print(f"[1] GET /models → HTTP {resp.status_code}")
        if resp.status_code == 200:
            try:
                ids = sorted({str(m.get("id", "")) for m in resp.json().get("data", [])})
            except Exception:
                ids = []
            print(f"    账号可见模型（{len(ids)} 个）：{ids}")
            if ids:
                if model in ids:
                    print(f"    ✅ 配置的模型名 `{model}` 在清单里")
                else:
                    ok = False
                    print(
                        f"    ⚠️ 配置的模型名 `{model}` **不在清单里**——"
                        "可能是已退役的名字（仍被接受的话也能用，见第 2 步实测）"
                    )
        else:
            print(f"    响应体：{resp.text[:400]}")
            ok = False

        # 2) 可选：最小 chat 调用（1 次极小消耗）——最权威的有效性证据
        if do_chat:
            body = {
                "model": model,
                "messages": [{"role": "user", "content": "1"}],
                "max_tokens": 1,
                "temperature": 0,
            }
            resp = await client.post(f"{base_url}/chat/completions", headers=headers, json=body)
            print(f"\n[2] POST /chat/completions（model={model}）→ HTTP {resp.status_code}")
            if resp.status_code == 200:
                used = resp.json().get("model", "")
                print(f"    ✅ 调用成功，服务端回报的实际模型名：{used}")
            else:
                ok = False
                print(f"    ❌ 调用失败，提供方原文：{resp.text[:400]}")
        else:
            print("\n[2] 跳过 chat 调用（加 --chat 可实测该模型名是否真的可用）")

    print(
        "\n结论："
        + ("模型名可用。" if ok else "模型名存疑/不可用——迁移模型名必须重跑冒烟后再上线。")
    )
    return 0 if ok else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="M6 模型名核对（不打印任何凭据）")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(os.getenv("GATEWAY_CONFIG_FILE", str(DEFAULT_CONFIG))),
        help="要核对的配置文件（默认 config/gateway.real.yaml）",
    )
    parser.add_argument(
        "--chat",
        action="store_true",
        help="额外发一次最小 chat 调用（消耗约 1 token），实测模型名是否可用",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(check(args.config, args.chat)))


if __name__ == "__main__":
    main()
