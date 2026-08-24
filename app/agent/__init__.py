"""Agent 核心化（阶段 2）：网关内 Agent 循环（AgentRunner）+ 步骤 Trace。

Agent 循环直接调用 registry.call_tool()，不走 HTTP 回环；工具列表先经过
ToolRouter 过滤（阶段 1），只注入 top-k，避免 context 膨胀。
"""
