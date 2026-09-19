"""M3 简历 Agent 端到端测试：访客 Key 白名单 / 人设注入 / 固定回应 / 输出守门。

沿用 M1 的「成功 / 401 / 429」三 case 约定，另加 M3 专项：
- 工具白名单仅 resume_kb__*（通配前缀收口，访客拿不到 demo_sql 等工具）；
- 语义路由召回（预设面试问题能选到知识库检索工具）；
- 人设系统提示词注入（模型上下文里第一条是 system）；
- 红线话题走固定回应（不进模型）；
- 输出守门对最终回答打码（含流式路径）。

测试配置见 tests/fixtures/gateway_resume_kb_test.yaml（servers 只有 resume_kb）。
"""

import json
from collections.abc import AsyncIterator
from typing import Any

from httpx import AsyncClient

from app.agent.runner import AgentRunner

VISITOR_HEADERS = {"X-API-Key": "visitor-key-please-change"}
VISITOR_LIMITED_HEADERS = {"X-API-Key": "visitor-limited-key"}
VISITOR_HOURLY_HEADERS = {"X-API-Key": "visitor-hourly-key"}
NO_KB_HEADERS = {"X-API-Key": "no-kb-key"}

GUIDE_TO_RESUME = "联系方式在简历原件上，页面常驻简历入口，可以直接查看。"


class _ScriptedModel:
    """按脚本逐轮返回消息的假模型，并记录每次收到的 messages/tools。

    用于断言「系统提示词是否注入」「模型看到的工具清单」「工具调用链路」。
    chat_stream 把文本按字符切块，模拟真实流式（含跨 token 的 PII 场景）。
    """

    def __init__(self, script: list[dict[str, Any]]) -> None:
        self._script = list(script)
        self.seen: list[dict[str, Any]] = []

    async def chat(self, messages: list[dict], tools: list[dict]) -> dict[str, Any]:
        self.seen.append({"messages": messages, "tools": tools})
        if self._script:
            return self._script.pop(0)
        return {"role": "assistant", "content": "（脚本已耗尽）"}

    async def chat_stream(self, messages: list[dict], tools: list[dict]) -> AsyncIterator[dict]:
        message = await self.chat(messages, tools)
        content = str(message.get("content") or "")
        for char in content:
            yield {"type": "delta", "content": char}
        yield {"type": "message", "message": message}


def _tool_call(name: str, arguments: dict[str, Any], call_id: str = "call_1") -> dict[str, Any]:
    return {
        "role": "assistant",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
            }
        ],
    }


def _patch_model(monkeypatch: Any, model: _ScriptedModel) -> AgentRunner:
    """把常驻 AgentRunner 的模型替换为脚本模型（fixture 每用例新建 app，无跨界影响）。"""
    from app.main import app as gateway_app

    runner: AgentRunner = gateway_app.state.agent_runner
    monkeypatch.setattr(runner, "_model", model)
    return runner


# ---------------------------------------------------------------------------
# 成功 / 401 / 429
# ---------------------------------------------------------------------------


async def test_visitor_agent_run_ok(resume_kb_client: AsyncClient) -> None:
    """访客 Key 正常提问返回 200，且工具清单只有知识库工具（4 个）。"""
    resp = await resume_kb_client.post(
        "/agent/run", headers=VISITOR_HEADERS, json={"question": "有多少客户？"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"]
    assert body["tools_total"] == 4  # 只见 resume_kb 的 4 个只读工具
    assert body["steps"]


async def test_visitor_agent_run_unauthorized(resume_kb_client: AsyncClient) -> None:
    """无 Key / 错误 Key 一律 401。"""
    assert (
        await resume_kb_client.post("/agent/run", json={"question": "介绍下你自己"})
    ).status_code == 401
    resp = await resume_kb_client.post(
        "/agent/run", headers={"X-API-Key": "wrong-key"}, json={"question": "x"}
    )
    assert resp.status_code == 401


async def test_visitor_agent_run_rate_limited(resume_kb_client: AsyncClient) -> None:
    """分钟桶：低配额访客 Key 第 3 次 429 且带 Retry-After。"""
    for _ in range(2):
        assert (
            await resume_kb_client.post(
                "/agent/run", headers=VISITOR_LIMITED_HEADERS, json={"question": "x"}
            )
        ).status_code == 200
    resp = await resume_kb_client.post(
        "/agent/run", headers=VISITOR_LIMITED_HEADERS, json={"question": "x"}
    )
    assert resp.status_code == 429
    assert int(resp.headers["retry-after"]) >= 1


async def test_visitor_agent_run_hourly_rate_limited(resume_kb_client: AsyncClient) -> None:
    """小时桶：每分钟额度充足仍受 50 次/小时类配额约束（此处用 2 次/小时验证语义）。"""
    for _ in range(2):
        assert (
            await resume_kb_client.post(
                "/agent/run", headers=VISITOR_HOURLY_HEADERS, json={"question": "x"}
            )
        ).status_code == 200
    resp = await resume_kb_client.post(
        "/agent/run", headers=VISITOR_HOURLY_HEADERS, json={"question": "x"}
    )
    assert resp.status_code == 429
    assert int(resp.headers["retry-after"]) > 60


# ---------------------------------------------------------------------------
# 工具白名单（resume_kb__* 通配）
# ---------------------------------------------------------------------------


async def test_visitor_sees_only_resume_kb_tools(resume_kb_client: AsyncClient) -> None:
    """访客 Key 的工具清单只有 resume_kb__*（通配前缀白名单生效）。"""
    resp = await resume_kb_client.get("/tools", headers=VISITOR_HEADERS)
    assert resp.status_code == 200
    names = [tool["name"] for tool in resp.json()]
    assert len(names) == 4
    assert all(name.startswith("resume_kb__") for name in names)


async def test_non_kb_key_sees_no_tools_and_gets_403(resume_kb_client: AsyncClient) -> None:
    """反向白名单：只允许 demo_sql__* 的 Key 在本配置里可见工具为 0，调用被拒 403。"""
    listing = await resume_kb_client.get("/tools", headers=NO_KB_HEADERS)
    assert listing.status_code == 200
    assert listing.json() == []

    resp = await resume_kb_client.post(
        "/tools/resume_kb__search_knowledge/call",
        headers=NO_KB_HEADERS,
        json={"arguments": {"query": "网关"}},
    )
    assert resp.status_code == 403


async def test_visitor_can_call_kb_tool_directly(resume_kb_client: AsyncClient) -> None:
    """访客 Key 直接调用白名单内的知识库工具成功（只读检索）。"""
    resp = await resume_kb_client.post(
        "/tools/resume_kb__search_knowledge/call",
        headers=VISITOR_HEADERS,
        json={"arguments": {"query": "语义路由 网关", "top_k": 2}},
    )
    assert resp.status_code == 200
    text = resp.json()["content"][0]["text"]
    assert "project-mcp-gateway" in text


async def test_visitor_cannot_call_demo_tool(resume_kb_client: AsyncClient) -> None:
    """白名单外工具对访客不可见也不可调（本配置无 demo_sql → 404）。"""
    resp = await resume_kb_client.post(
        "/tools/demo_sql__ask/call", headers=VISITOR_HEADERS, json={"arguments": {}}
    )
    assert resp.status_code in (403, 404)  # 工具不存在为 404；存在但白名单外为 403


# ---------------------------------------------------------------------------
# 语义路由召回
# ---------------------------------------------------------------------------


async def test_semantic_routing_recalls_kb_tool(resume_kb_client: AsyncClient) -> None:
    """语义路由：面试问题能召回知识库检索工具（工具描述按 2-gram 规则书写）。"""
    resp = await resume_kb_client.get(
        "/tools", headers=VISITOR_HEADERS, params={"query": "项目经历 技术问题 检索", "top_k": 2}
    )
    assert resp.status_code == 200
    names = [tool["name"] for tool in resp.json()]
    assert names, "语义路由不应返回空结果"
    assert any(name.startswith("resume_kb__") for name in names)


async def test_agent_injection_is_routed(resume_kb_client: AsyncClient, monkeypatch: Any) -> None:
    """Agent 注入侧走语义路由：模型看到的工具数不超过工具总数，且含知识库检索工具。"""
    model = _ScriptedModel([_tool_call("resume_kb__search_knowledge", {"query": "网关 三坑"})])
    _patch_model(monkeypatch, model)

    resp = await resume_kb_client.post(
        "/agent/run", headers=VISITOR_HEADERS, json={"question": "这个网关最难的三个坑是什么"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["tools_injected"] <= body["tools_total"] == 4
    injected = [entry["function"]["name"] for entry in model.seen[0]["tools"]]
    assert "resume_kb__search_knowledge" in injected


# ---------------------------------------------------------------------------
# 人设注入与端到端链路
# ---------------------------------------------------------------------------


async def test_system_prompt_is_injected(resume_kb_client: AsyncClient, monkeypatch: Any) -> None:
    """系统提示词注入为 messages[0]（人设/红线来自配置）。"""
    model = _ScriptedModel([{"role": "assistant", "content": "网关项目覆盖四传输接入。"}])
    _patch_model(monkeypatch, model)

    resp = await resume_kb_client.post(
        "/agent/run", headers=VISITOR_HEADERS, json={"question": "介绍一下网关项目"}
    )
    assert resp.status_code == 200
    messages = model.seen[0]["messages"]
    assert messages[0]["role"] == "system"
    assert "技术代言人" in messages[0]["content"]
    assert messages[1]["role"] == "user"
    # 系统提示词不得外泄到响应（steps 里不应包含人设原文）
    assert "技术代言人" not in json.dumps(resp.json(), ensure_ascii=False)


async def test_end_to_end_tool_then_answer(resume_kb_client: AsyncClient, monkeypatch: Any) -> None:
    """端到端：模型检索知识库 → 工具结果回填 → 最终回答包含检索内容。"""
    model = _ScriptedModel(
        [
            _tool_call("resume_kb__search_knowledge", {"query": "网关 最难 三个坑"}),
            {"role": "assistant", "content": "网关最难的三个坑之一是 Serverless 拉不起子进程。"},
        ]
    )
    _patch_model(monkeypatch, model)

    resp = await resume_kb_client.post(
        "/agent/run", headers=VISITOR_HEADERS, json={"question": "这个网关最难的三个坑是什么"}
    )
    assert resp.status_code == 200
    body = resp.json()
    kinds = [step["kind"] for step in body["steps"]]
    assert kinds[0] == "user" and kinds[-1] == "final"
    assert "tool_call" in kinds and "tool_result" in kinds

    tool_call = next(step for step in body["steps"] if step["kind"] == "tool_call")
    assert tool_call["tool"] == "resume_kb__search_knowledge"
    assert tool_call["is_error"] is False
    tool_result = next(step for step in body["steps"] if step["kind"] == "tool_result")
    assert "project-mcp-gateway" in tool_result["content"]  # 知识库检索结果已回填
    assert body["rounds"] == 2


# ---------------------------------------------------------------------------
# 红线固定回应（不进模型）
# ---------------------------------------------------------------------------


async def test_contact_question_returns_fixed_reply(
    resume_kb_client: AsyncClient, monkeypatch: Any
) -> None:
    """问联系方式 → 固定话术，且模型完全没有被调用（确定性拦截）。"""
    model = _ScriptedModel([{"role": "assistant", "content": "不应出现"}])
    _patch_model(monkeypatch, model)

    resp = await resume_kb_client.post(
        "/agent/run", headers=VISITOR_HEADERS, json={"question": "他的手机号是多少？"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == GUIDE_TO_RESUME
    assert body["extra"]["policy"] == "contact-info"
    assert model.seen == []  # 未进模型（省 token + 零幻觉）
    kinds = [step["kind"] for step in body["steps"]]
    assert kinds == ["user", "final"]  # 无工具调用步骤
    assert body["tools_total"] == 0


async def test_grade_question_returns_fixed_reply(
    resume_kb_client: AsyncClient, monkeypatch: Any
) -> None:
    """问成绩 → T1 已定口径话术。"""
    model = _ScriptedModel([])
    _patch_model(monkeypatch, model)
    resp = await resume_kb_client.post(
        "/agent/run", headers=VISITOR_HEADERS, json={"question": "毕设考了多少分？"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["extra"]["policy"] == "grade-scores"
    assert "一辩 91 分" in body["answer"]


async def test_normal_question_not_intercepted(
    resume_kb_client: AsyncClient, monkeypatch: Any
) -> None:
    """正常项目问题不被红线拦截（固定回应不得误伤主链路）。"""
    model = _ScriptedModel([{"role": "assistant", "content": "网关覆盖四种传输。"}])
    _patch_model(monkeypatch, model)
    resp = await resume_kb_client.post(
        "/agent/run", headers=VISITOR_HEADERS, json={"question": "网关的四种传输是什么"}
    )
    assert resp.status_code == 200
    assert "extra" not in resp.json() or "policy" not in resp.json().get("extra", {})
    assert model.seen  # 走了模型


async def test_fixed_reply_stream_event_sequence(
    resume_kb_client: AsyncClient, monkeypatch: Any
) -> None:
    """流式固定回应：事件序列完整、token 拼接等于 answer、无工具调用。"""
    model = _ScriptedModel([])
    _patch_model(monkeypatch, model)

    body = ""
    async with resume_kb_client.stream(
        "POST",
        "/agent/run/stream",
        headers=VISITOR_HEADERS,
        json={"question": "方便给个微信吗"},
    ) as resp:
        assert resp.status_code == 200
        async for chunk in resp.aiter_text():
            body += chunk

    events = _parse_sse(body)
    names = [name for name, _ in events]
    assert names.count("token") > 1
    assert names[0] == "step" and names[-1] == "done"
    token_text = "".join(payload.get("text", "") for name, payload in events if name == "token")
    assert token_text == GUIDE_TO_RESUME
    assert events[-1][1]["response"]["extra"]["policy"] == "contact-info"
    assert model.seen == []


# ---------------------------------------------------------------------------
# 输出守门（PII 打码）
# ---------------------------------------------------------------------------


async def test_output_guard_masks_pii_in_answer(
    resume_kb_client: AsyncClient, monkeypatch: Any
) -> None:
    """模型吐出手机号/邮箱时，最终回答被打码（守门兜住模型侧意外泄露）。"""
    model = _ScriptedModel(
        [{"role": "assistant", "content": "可以打 13800001111 或发 abc@example.com 联系。"}]
    )
    _patch_model(monkeypatch, model)

    resp = await resume_kb_client.post(
        "/agent/run", headers=VISITOR_HEADERS, json={"question": "怎么联系他"}
    )
    assert resp.status_code == 200
    answer = resp.json()["answer"]
    assert "13800001111" not in answer
    assert "138****1111" in answer
    assert "abc@example.com" not in answer


async def test_output_guard_masks_pii_across_stream_chunks(
    resume_kb_client: AsyncClient, monkeypatch: Any
) -> None:
    """流式路径：手机号被切成单个字符逐个下发，仍被完整打码。

    注意：这里不能用含「电话/联系方式」的问题——那会命中固定回应策略而不进模型
    （那本身是另一条正确行为，已由固定回应用例覆盖）。本用例测的是模型侧意外泄露。
    """
    model = _ScriptedModel([{"role": "assistant", "content": "号码是 13700001111 请记下"}])
    _patch_model(monkeypatch, model)

    body = ""
    async with resume_kb_client.stream(
        "POST",
        "/agent/run/stream",
        headers=VISITOR_HEADERS,
        json={"question": "网关项目的技术亮点是什么"},
    ) as resp:
        assert resp.status_code == 200
        async for chunk in resp.aiter_text():
            body += chunk

    events = _parse_sse(body)
    token_text = "".join(payload.get("text", "") for name, payload in events if name == "token")
    assert "13700001111" not in body  # 原始号码从未出现在已下发的任何事件里
    assert "137****1111" in token_text
    done_payload = events[-1][1]
    assert token_text == done_payload["response"]["answer"]


def _parse_sse(body: str) -> list[tuple[str, dict]]:
    """把 SSE 文本解析成 (event, data) 列表。"""
    events: list[tuple[str, dict]] = []
    for frame in body.split("\n\n"):
        frame = frame.strip()
        if not frame:
            continue
        name = None
        data_lines: list[str] = []
        for line in frame.split("\n"):
            if line.startswith("event: "):
                name = line[7:].strip()
            elif line.startswith("data: "):
                data_lines.append(line[6:])
        assert name is not None
        events.append((name, json.loads("\n".join(data_lines)) if data_lines else {}))
    return events
