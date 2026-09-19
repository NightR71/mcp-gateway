"""AgentRunner 单元测试（阶段 2）：完整调用链 / 直接回答 / 轮数上限 / 步骤 Trace。

使用 inprocess 传输的 demo_sql server（进程内加载，不拉子进程），
MockModel 保证确定性，无需任何 LLM Key。
"""

from collections.abc import AsyncIterator

import pytest

from app.agent.models import MockModel
from app.agent.runner import MAX_ROUNDS_FALLBACK, AgentRunner, tool_to_function_schema
from app.agent.schemas import AgentRequest, AgentResponse
from app.config import MCPServerConfig
from app.mcp.registry import ToolRegistry
from app.mcp.tool_router import ToolRouter
from app.schemas.auth import APIKeyInfo


def _demo_config() -> MCPServerConfig:
    return MCPServerConfig(
        name="demo_sql",
        transport="inprocess",
        module="servers.demo_sql_server.server:server",
    )


@pytest.fixture
async def registry() -> AsyncIterator[ToolRegistry]:
    r = ToolRegistry((_demo_config(),))
    await r.connect_all()
    yield r
    await r.close()


def _runner(
    registry: ToolRegistry, *, model: MockModel | None = None, max_rounds: int = 8
) -> AgentRunner:
    return AgentRunner(
        registry,
        model or MockModel(),
        router=ToolRouter(top_k=10, min_tools=2),
        max_rounds=max_rounds,
        routing_top_k=10,
    )


async def test_full_chain_ask_customers(registry: ToolRegistry) -> None:
    """「有多少客户？」完整调用链：路由 → 工具调用 → 最终回答。"""
    runner = _runner(registry)
    response = await runner.run(AgentRequest(question="有多少客户？"))
    assert isinstance(response, AgentResponse)
    assert "SELECT COUNT(*)" in response.answer  # mock 回填了 ask 的查询结果
    assert "| 5 |" in response.answer  # demo 库 5 位客户
    assert response.tools_total == 4
    assert response.tools_injected == 1  # 语义路由：「客户」只命中 ask
    assert response.rounds == 2  # 第一轮选工具，第二轮回填结果


async def test_direct_answer_branch(registry: ToolRegistry) -> None:
    """模型直接回答分支：不调工具，一步出结果。"""
    runner = _runner(registry, model=MockModel(direct=True))
    response = await runner.run(AgentRequest(question="你好"))
    assert response.rounds == 1
    assert "5 位客户" in response.answer
    kinds = [s.kind for s in response.steps]
    assert kinds == ["user", "tool_select", "final"]


async def test_max_rounds_limit(registry: ToolRegistry) -> None:
    """轮数上限：模型一直要调工具时，到达上限返回兜底文案。"""
    runner = _runner(registry, max_rounds=1)
    response = await runner.run(AgentRequest(question="有多少客户？"))
    assert response.rounds == 1
    assert response.answer == MAX_ROUNDS_FALLBACK
    assert response.steps[-1].kind == "final"


async def test_steps_kind_and_order(registry: ToolRegistry) -> None:
    """steps 类型与顺序：user → tool_select → tool_call → tool_result → final。"""
    response = await _runner(registry).run(AgentRequest(question="有多少客户？"))
    kinds = [s.kind for s in response.steps]
    assert kinds[0] == "user"
    assert kinds[1] == "tool_select"
    assert "tool_call" in kinds
    assert "tool_result" in kinds
    assert kinds[-1] == "final"
    assert kinds.index("tool_call") < kinds.index("tool_result") < kinds.index("final")
    tool_call = next(s for s in response.steps if s.kind == "tool_call")
    assert tool_call.tool == "demo_sql__ask"
    assert tool_call.latency_ms is not None and tool_call.latency_ms >= 0


async def test_tools_injected_not_exceed_total(registry: ToolRegistry) -> None:
    """tools_injected <= tools_total 恒成立。"""
    response = await _runner(registry).run(AgentRequest(question="有多少客户？"))
    assert response.tools_injected <= response.tools_total
    assert 0 < response.tools_total <= 100


async def test_runner_filters_tools_by_key_whitelist(registry: ToolRegistry) -> None:
    """M1 §5.3：Agent 注入按 Key 白名单过滤——模型只能看见白名单内工具。"""
    limited = APIKeyInfo(key="limited", name="limited", allowed_tools=["demo_sql__ask"])
    runner = _runner(registry)

    response = await runner.run(AgentRequest(question="有多少客户？"), key=limited)
    assert response.tools_total == 1  # 白名单 Key 只可见 demo_sql__ask（不再泄露全量清单）
    assert response.tools_injected == 1
    assert "SELECT COUNT(*)" in response.answer  # 白名单内链路完整可用

    # 不传 Key：行为与旧版一致（全量工具）
    response = await runner.run(AgentRequest(question="有多少客户？"))
    assert response.tools_total == 4


async def test_runner_rejects_out_of_whitelist_tool_call(registry: ToolRegistry) -> None:
    """M1 §5.3：模型被诱导生成白名单外调用时，registry 收口校验拒绝并记录 error 步骤。"""
    import json as jsonlib

    class _ForceRunSqlModel(MockModel):
        async def chat(self, messages, tools):
            return {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_forced",
                        "type": "function",
                        "function": {
                            "name": "demo_sql__run_sql",
                            "arguments": jsonlib.dumps({"sql": "SELECT 1"}),
                        },
                    }
                ],
            }

    limited = APIKeyInfo(key="limited", name="limited", allowed_tools=["demo_sql__ask"])
    response = await _runner(registry, model=_ForceRunSqlModel()).run(
        AgentRequest(question="查所有客户"), key=limited
    )

    tool_call = next(s for s in response.steps if s.kind == "tool_call")
    assert tool_call.tool == "demo_sql__run_sql"
    assert tool_call.is_error is True
    assert "白名单" in tool_call.content  # 拒绝原因进入步骤 Trace
    assert "5" not in tool_call.content  # SQL 未被执行（无查询结果）


async def test_tool_error_does_not_break_loop(registry: ToolRegistry) -> None:
    """工具调用失败被记录为 error step，不中断 Agent 循环（最终由轮数上限兜底）。"""

    class _ErrorModel(MockModel):
        async def chat(self, messages, tools):
            return {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_err",
                        "type": "function",
                        "function": {"name": "demo_sql__no_such_tool", "arguments": "{}"},
                    }
                ],
            }

    response = await _runner(registry, model=_ErrorModel()).run(AgentRequest(question="测试"))
    error_step = next(s for s in response.steps if s.kind == "tool_call" and s.is_error)
    assert "demo_sql__no_such_tool" in error_step.content  # 未知工具错误被记录
    assert response.steps[-1].kind == "final"


async def test_internal_tool_error_is_opaque_in_steps(registry: ToolRegistry) -> None:
    """M1 §5.3：工具调用抛内部异常时，步骤内容不得带出原文（只给不透明文案 + 关联 ID）。

    回归背景：路由层的脱敏看不到 Agent 循环内被吞掉的异常，步骤内容会随响应与 SSE
    直达公网客户端（且回填模型上下文）——必须在循环内同样脱敏。
    """
    secret = "INTERNAL_SDK_PATH=/opt/secret/gateway.py:42"

    class _BoomModel(MockModel):
        async def chat(self, messages, tools):
            return {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_boom",
                        "type": "function",
                        "function": {"name": "demo_sql__ask", "arguments": '{"question": "x"}'},
                    }
                ],
            }

    async def boom(name: str, arguments: dict, key=None):
        raise RuntimeError(secret)

    registry.call_tool = boom  # 注入一次性替身（fixture 每用例新建 registry，无跨界影响）

    response = await _runner(registry, model=_BoomModel()).run(AgentRequest(question="测试"))

    error_step = next(s for s in response.steps if s.kind == "tool_call" and s.is_error)
    assert "服务内部错误（关联ID: " in error_step.content
    assert secret not in error_step.content
    assert secret not in response.model_dump_json()  # 整份响应（含 tool_result 步骤）都不含原文


async def test_expected_tool_errors_keep_readable_message(registry: ToolRegistry) -> None:
    """预期内工具错误（白名单外）仍保留可读文案——脱敏不得误伤可诊断性。"""
    limited = APIKeyInfo(key="limited", name="limited", allowed_tools=["demo_sql__ask"])

    class _ForceRunSqlModel(MockModel):
        async def chat(self, messages, tools):
            return {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_forced",
                        "type": "function",
                        "function": {
                            "name": "demo_sql__run_sql",
                            "arguments": '{"sql": "SELECT 1"}',
                        },
                    }
                ],
            }

    response = await _runner(registry, model=_ForceRunSqlModel()).run(
        AgentRequest(question="查询"), key=limited
    )
    error_step = next(s for s in response.steps if s.kind == "tool_call" and s.is_error)
    assert "白名单" in error_step.content  # 明确原因保留
    assert "关联ID" not in error_step.content  # 不是内部异常，不该被脱敏


async def test_tool_to_function_schema_matches_openai_naming(registry: ToolRegistry) -> None:
    """ToolInfo → OpenAI function schema：命名空间名满足 ^[a-zA-Z0-9_-]+$。"""
    import re

    tool = next(t for t in registry.list_tools() if t.name == "demo_sql__ask")
    function = tool_to_function_schema(tool)
    assert re.fullmatch(r"[a-zA-Z0-9_-]+", function["name"]) is not None
    assert function["parameters"]["type"] == "object"
    assert "question" in function["parameters"]["properties"]
