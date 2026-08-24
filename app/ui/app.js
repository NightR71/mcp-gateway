/* MCP Agent Workbench 前端逻辑（纯原生 JS，无框架无 CDN）。
 *
 * - 调用 POST /agent/run（阶段 3 同步版；阶段 4 将切到 /agent/run/stream）
 * - 按 AgentResponse.steps 的 kind 渲染时间线四种卡片
 * - Key 只从页面输入框读、只放请求头，不落 localStorage
 * - 自检区：健康检查 / 列出工具直查 /health、/tools
 */

"use strict";

const timeline = document.getElementById("timeline");
const questionInput = document.getElementById("question");
const sendBtn = document.getElementById("send-btn");
const healthBtn = document.getElementById("health-btn");
const toolsBtn = document.getElementById("tools-btn");
const keyInput = document.getElementById("api-key");
const toolCount = document.getElementById("tool-count");
const roundInfo = document.getElementById("round-info");

const KIND_LABEL = {
  user: "User",
  tool_select: "Agent",
  tool_call: "Agent",
  tool_result: "Tool Result",
  final: "Agent",
};

function addStep(kind, content, opts = {}) {
  const step = document.createElement("div");
  step.className = "step " + kind + (opts.isError ? " error" : "");
  const head = document.createElement("div");
  head.className = "head";
  const kindEl = document.createElement("span");
  kindEl.className = "kind";
  kindEl.textContent = KIND_LABEL[kind] || kind;
  head.appendChild(kindEl);
  if (opts.tool) {
    const toolEl = document.createElement("span");
    toolEl.className = "pill";
    toolEl.textContent = opts.tool;
    head.appendChild(toolEl);
  }
  if (opts.latencyMs != null) {
    const meta = document.createElement("span");
    meta.className = "meta";
    meta.textContent = Math.round(opts.latencyMs) + "ms";
    head.appendChild(meta);
  }
  step.appendChild(head);
  if (content) {
    const body = document.createElement("div");
    body.className = "body" + (opts.isError ? " error-text" : "");
    body.textContent = content;
    step.appendChild(body);
  }
  timeline.appendChild(step);
  step.scrollIntoView({ block: "nearest" });
  return step;
}

function apiKey() {
  return keyInput.value.trim();
}

function headers() {
  return { "X-API-Key": apiKey(), "Content-Type": "application/json" };
}

async function requestJson(url, options) {
  const resp = await fetch(url, options);
  if (resp.status === 401 || resp.status === 429 || resp.status === 503) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(`HTTP ${resp.status}：${body.detail || resp.statusText}`);
  }
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(`HTTP ${resp.status}：${body.detail || resp.statusText}`);
  }
  return resp.json();
}

async function runAgent(question) {
  sendBtn.disabled = true;
  addStep("user", question);
  const placeholder = addStep("agent", "正在选择工具并调用网关...");
  try {
    await runAgentStream(question, placeholder);
  } catch (err) {
    placeholder.remove();
    addStep("final", String(err.message || err), { isError: true });
  } finally {
    sendBtn.disabled = false;
  }
}

/* 阶段 4：POST /agent/run/stream 逐帧读取 SSE（\n\n 分帧），
 * token 逐字追加，step 实时插入时间线卡片。 */
async function runAgentStream(question, placeholder) {
  const resp = await fetch("/agent/run/stream", {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({ question }),
  });
  if (resp.status === 401 || resp.status === 429 || resp.status === 503) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(`HTTP ${resp.status}：${body.detail || resp.statusText}`);
  }
  if (!resp.ok || !resp.body) {
    throw new Error(`HTTP ${resp.status}：无法建立流式连接`);
  }
  placeholder.remove();
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalEl = null;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let sep;
    while ((sep = buffer.indexOf("\n\n")) >= 0) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      finalEl = handleFrame(frame, finalEl) || finalEl;
    }
  }
}

/* 解析单个 SSE 帧（event: xxx\ndata: {...}），返回 final 步骤的元素（供 token 追加）。 */
function handleFrame(frame, finalEl) {
  let eventName = null;
  const dataLines = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith("event: ")) eventName = line.slice(7).trim();
    else if (line.startsWith("data: ")) dataLines.push(line.slice(6));
  }
  if (!eventName || dataLines.length === 0) return null;
  const payload = JSON.parse(dataLines.join("\n"));
  if (eventName === "step") {
    return renderStreamStep(payload.step, finalEl);
  }
  if (eventName === "token" && payload.text) {
    if (!finalEl) finalEl = addStep("final", "");
    const body = finalEl.querySelector(".body");
    body.textContent += payload.text;
    finalEl.scrollIntoView({ block: "nearest" });
    return finalEl;
  }
  if (eventName === "done" && payload.response) {
    const resp = payload.response;
    toolCount.textContent = `工具: ${resp.tools_total}/${resp.tools_total} · 注入: ${resp.tools_injected}`;
    roundInfo.textContent = `轮数: ${resp.rounds}`;
    roundInfo.classList.remove("hidden");
  }
  return null;
}

/* 流式 step 事件：复用渲染逻辑；token 前的 final step 卡作为逐字追加容器。 */
function renderStreamStep(step, finalEl) {
  if (step.kind === "tool_call" && !step.is_error) {
    return addStep("gateway", `鉴权 ✓ 限流 ✓ 执行 ${step.tool}`, {
      tool: step.tool,
      latencyMs: step.latency_ms,
    });
  }
  if (step.kind === "tool_result") {
    const card = addStep("tool-result", `工具 ${step.tool} 返回：`, {
      tool: step.tool,
      latencyMs: step.latency_ms,
      isError: step.is_error,
    });
    const results = document.createElement("div");
    results.className = "tool-results";
    results.textContent = step.content;
    card.appendChild(results);
    return null;
  }
  if (step.kind === "final") {
    if (finalEl) return finalEl;
    return addStep("final", step.content || "", {
      isError: step.is_error,
      latencyMs: step.latency_ms,
    });
  }
  return addStep(step.kind, step.content, {
    isError: step.is_error,
    latencyMs: step.latency_ms,
  });
}

async function healthCheck() {
  try {
    const body = await requestJson("/health");
    addStep("gateway", `健康检查 OK：${JSON.stringify(body)}`);
  } catch (err) {
    addStep("gateway", String(err.message || err), { isError: true });
  }
}

async function listTools() {
  try {
    const tools = await requestJson("/tools", { headers: headers() });
    const names = tools.map((t) => t.name).join("\n");
    addStep("gateway", `网关聚合 ${tools.length} 个工具：\n${names}`);
  } catch (err) {
    addStep("gateway", String(err.message || err), { isError: true });
  }
}

function onSend() {
  const question = questionInput.value.trim();
  if (!question) return;
  questionInput.value = "";
  runAgent(question);
}

sendBtn.addEventListener("click", onSend);
questionInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") onSend();
});
healthBtn.addEventListener("click", healthCheck);
toolsBtn.addEventListener("click", listTools);
