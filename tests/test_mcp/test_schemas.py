"""协议层 Pydantic 模型测试。"""

from app.mcp.schemas import ToolCallRequest, ToolCallResult, ToolInfo


def test_tool_info_defaults() -> None:
    t = ToolInfo(name="s__t", original_name="t", server="s")
    assert t.description == ""
    assert t.input_schema == {}


def test_call_request_default_arguments() -> None:
    assert ToolCallRequest().arguments == {}


def test_call_result_roundtrip() -> None:
    r = ToolCallResult(content=[{"type": "text", "text": "ok"}], is_error=False)
    assert r.content[0]["text"] == "ok"
    assert not r.is_error
