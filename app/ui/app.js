/* MCP Agent Workbench 前端逻辑（纯原生 JS，无框架无 CDN）。
 *
 * - 左侧时间线：POST /agent/run/stream SSE 渲染（User → Agent → Gateway → Tool Result → 最终回答）
 * - 富文本渲染：SQL 代码块高亮、Markdown 表格、列表（工具结果 / 最终回答）
 * - 右侧「运行流程」面板：SVG 动画演示完整调用链路（由「发送」触发，与真实请求异步，支持暂停/重播）
 * - Key 只从页面输入框读、只放请求头，不落 localStorage
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
  tool_call: "Gateway",
  tool_result: "Tool Result",
  final: "Agent",
  gateway: "Gateway",
  agent: "Agent",
};

const KIND_CLASS = {
  user: "user",
  tool_select: "agent",
  tool_call: "gateway",
  tool_result: "tool-result",
  final: "final",
  gateway: "gateway",
  agent: "agent",
};

function addStep(kind, content, opts = {}) {
  const step = document.createElement("div");
  step.className = "step " + (KIND_CLASS[kind] || kind) + (opts.isError ? " error" : "");
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
  const body = document.createElement("div");
  body.className = "body" + (opts.isError ? " error-text" : "");
  if (opts.rich) renderRich(body, content);
  else if (content) body.textContent = content;
  step.appendChild(body);
  timeline.appendChild(step);
  step.scrollIntoView({ block: "nearest" });
  return step;
}

function setLatency(stepEl, ms) {
  if (ms == null || !stepEl) return;
  let meta = stepEl.querySelector(".meta");
  if (!meta) {
    meta = document.createElement("span");
    meta.className = "meta";
    stepEl.querySelector(".head").appendChild(meta);
  }
  meta.textContent = Math.round(ms) + "ms";
}

/* ---------- 富文本渲染：``` 代码块（SQL 高亮）/ Markdown 表格 / - 列表 / 段落 ---------- */

function escapeHtml(s) {
  return String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}

/* SQL 极简高亮：字符串整体着色，其余部分按关键字/函数/数字着色 */
function highlightSql(src) {
  return src
    .split(/('[^']*')/g)
    .map((part, i) => {
      if (i % 2 === 1) return `<span class="tok-str">${part}</span>`;
      return part
        .replace(
          /\b(SELECT|FROM|WHERE|GROUP BY|ORDER BY|LIMIT|JOIN|INNER|LEFT|ON|AS|ASC|DESC|AND|OR|NOT|NULL|WITH|DISTINCT|HAVING|BY)\b/g,
          '<span class="tok-kw">$1</span>'
        )
        .replace(/\b(COUNT|SUM|AVG|ROUND|MAX|MIN|COALESCE)\b(?=\s*\()/g, '<span class="tok-fn">$1</span>')
        .replace(/\b(\d+(?:\.\d+)?)\b/g, '<span class="tok-num">$1</span>');
    })
    .join("");
}

function renderRich(container, text) {
  const lines = String(text).replace(/\r\n/g, "\n").split("\n");
  const isFence = (s) => /^\s*```/.test(s);
  const isTableRow = (s) => /^\s*\|.*\|\s*$/.test(s);
  const isBullet = (s) => /^\s*-\s+/.test(s);
  const cells = (row) =>
    row
      .trim()
      .replace(/^\|/, "")
      .replace(/\|$/, "")
      .split("|")
      .map((c) => c.trim());
  let html = "";
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (isFence(line)) {
      const lang = line.trim().replace(/^```/, "").trim();
      i += 1;
      const code = [];
      while (i < lines.length && !isFence(lines[i])) code.push(lines[i++]);
      i += 1; // 跳过收尾 ```
      const escaped = escapeHtml(code.join("\n"));
      const looksSql = lang.toLowerCase() === "sql" || /\bselect\b|\bwith\b/i.test(escaped);
      const highlighted = looksSql ? highlightSql(escaped) : escaped;
      html +=
        `<pre class="code-block">` +
        (lang ? `<span class="code-lang">${escapeHtml(lang)}</span>` : "") +
        `<code>${highlighted}</code></pre>`;
      continue;
    }
    if (isTableRow(line)) {
      const rows = [];
      while (i < lines.length && isTableRow(lines[i])) rows.push(lines[i++]);
      const isSep = (row) => cells(row).every((c) => /^:?-{2,}:?$/.test(c));
      const head = cells(rows[0]);
      const bodyRows = rows.slice(1).filter((r) => !isSep(r)).map(cells);
      html +=
        `<table class="md-table"><thead><tr>` +
        head.map((c) => `<th>${escapeHtml(c)}</th>`).join("") +
        `</tr></thead><tbody>` +
        bodyRows.map((r) => `<tr>${r.map((c) => `<td>${escapeHtml(c)}</td>`).join("")}</tr>`).join("") +
        `</tbody></table>`;
      continue;
    }
    if (isBullet(line)) {
      const items = [];
      while (i < lines.length && isBullet(lines[i])) items.push(lines[i++].replace(/^\s*-\s+/, ""));
      html += `<ul>${items.map((t) => `<li>${escapeHtml(t)}</li>`).join("")}</ul>`;
      continue;
    }
    if (line.trim() === "") {
      i += 1;
      continue;
    }
    const para = [];
    while (
      i < lines.length &&
      lines[i].trim() !== "" &&
      !isFence(lines[i]) &&
      !isTableRow(lines[i]) &&
      !isBullet(lines[i])
    ) {
      para.push(lines[i++]);
    }
    html += `<p>${para.map((l) => escapeHtml(l)).join("<br>")}</p>`;
  }
  container.innerHTML = html;
}

/* ---------- 网关请求 ---------- */

function apiKey() {
  return keyInput.value.trim();
}

function headers() {
  return { "X-API-Key": apiKey(), "Content-Type": "application/json" };
}

async function requestJson(url, options) {
  const resp = await fetch(url, options);
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

/* POST /agent/run/stream 逐帧读取 SSE（\n\n 分帧），
 * token 逐字追加到最终回答卡，step 实时插入时间线卡片。 */
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

/* 解析单个 SSE 帧（event: xxx\ndata: {...})。
 * 返回值只允许是「最终回答卡」元素（token 追加容器），其余一律返回 null。 */
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
  if (eventName === "error") {
    return addStep("final", `流式执行出错：${payload.detail || payload.error || "未知错误"}`, {
      isError: true,
    });
  }
  if (eventName === "done" && payload.response) {
    const resp = payload.response;
    toolCount.textContent = `工具: ${resp.tools_total}/${resp.tools_total} · 注入: ${resp.tools_injected}`;
    roundInfo.textContent = `轮数: ${resp.rounds}`;
    roundInfo.classList.remove("hidden");
  }
  return null;
}

/* 流式 step 事件渲染。只有 final 卡可作为返回值（供 token 追加）；
 * user 步骤跳过（发送时本地已渲染，避免重复卡片）。 */
function renderStreamStep(step, finalEl) {
  if (step.kind === "user") {
    return null;
  }
  if (step.kind === "tool_call") {
    addStep(
      "gateway",
      step.is_error ? step.content || "工具调用失败" : `鉴权 ✓ 限流 ✓ 执行 ${step.tool}`,
      { tool: step.tool, latencyMs: step.latency_ms, isError: step.is_error }
    );
    return null;
  }
  if (step.kind === "tool_result") {
    const card = addStep("tool-result", `工具 ${step.tool} 返回：`, {
      tool: step.tool,
      latencyMs: step.latency_ms,
      isError: step.is_error,
    });
    const results = document.createElement("div");
    results.className = "tool-results rich";
    renderRich(results, step.content);
    card.appendChild(results);
    return null;
  }
  if (step.kind === "final") {
    let card = finalEl;
    if (!card) card = addStep("final", "", { isError: step.is_error });
    const body = card.querySelector(".body");
    body.classList.toggle("error-text", Boolean(step.is_error));
    body.innerHTML = "";
    renderRich(body, step.content || "");
    setLatency(card, step.latency_ms);
    card.scrollIntoView({ block: "nearest" });
    return card;
  }
  addStep(step.kind, step.content, {
    isError: step.is_error,
    latencyMs: step.latency_ms,
  });
  return null;
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

/* ---------- 右侧「运行流程」演示动画（与真实请求异步，仅由发送/重播触发） ---------- */

const flowStage = document.getElementById("flow-stage");
const flowDot = document.getElementById("flow-dot");
const flowStatus = document.getElementById("flow-status");
const flowCaption = document.getElementById("flow-caption-text");
const flowPauseBtn = document.getElementById("flow-pause-btn");
const flowReplayBtn = document.getElementById("flow-replay-btn");
const flowPanel = document.getElementById("flow-panel");

const FLOW_TRAVEL_MS = 750;
const FLOW_HOLD_MS = 420;

/* 每段：路径 id、起点/终点节点、到达后浮现的小标签（节点 id + 文案）、底部字幕 */
const FLOW_SEGMENTS = [
  {
    path: "fp12", from: "n1", to: "n2", tip: "n2", label: "鉴权 · 限流",
    cap: "① 用户提问进入网关：校验 X-API-Key（失败 401）→ 令牌桶限流（超限 429 + Retry-After）",
  },
  {
    path: "fp23", from: "n2", to: "n3", tip: "n3", label: "筛选工具",
    cap: "② ToolRouter 对问题分词打分，只注入 top-k 相关工具，避免全量塞进模型上下文",
  },
  {
    path: "fp34", from: "n3", to: "n4", tip: "n4", label: "选择工具",
    cap: "③ Agent · 模型决定调用哪个工具：默认 Mock 剧本；配置 GATEWAY_AGENT_API_KEY 后为真实 LLM 推理",
  },
  {
    path: "fp45", from: "n4", to: "n5", tip: "n5", label: "命名空间路由",
    cap: "④ Registry 按 {server}__{tool} 命名空间把工具调用路由到来源 MCP Server",
  },
  {
    path: "fp56", from: "n5", to: "n6", tip: "n6", label: "MCP 协议调用",
    cap: "⑤ MCP Client 经 stdio / HTTP / inprocess 传输发出协议调用（30s 总超时保护）",
  },
  {
    path: "fp67", from: "n6", to: "n7", tip: "n7", label: "NL2SQL 生成 SQL",
    cap: "⑥ demo_sql_server 执行 ask：hybrid 模式规则命中直接出 SQL，未命中才降级 LLM",
  },
  {
    path: "fp78", from: "n7", to: "n8", tip: "n8", label: "只读查询",
    cap: "⑦ 只读校验（仅 SELECT/WITH、拒绝危险关键字）后查询 SQLite，最多返回 50 行",
  },
  {
    path: "fpRet", from: "n8", to: "n4", tip: "n4", label: "结果回填", mode: "ret",
    cap: "⑧ 工具结果沿链路返回，回填模型组织最终回答",
  },
  {
    path: "fpAns", from: "n4", to: "n1", tip: "n1", label: "流式回答", mode: "ans",
    cap: "⑨ SSE 逐字流式输出最终回答；Prometheus 计数、structlog 记录全程事件",
  },
];

/* 小标签锚点（viewBox 坐标 → 百分比定位，随 SVG 等比缩放） */
const FLOW_TIP_POS = {
  n1: { x: 262, y: 40, transform: "translate(0, -50%)" },
  n2: { x: 218, y: 92, transform: "translate(-50%, -100%)" },
  n3: { x: 162, y: 172, transform: "translate(-50%, -100%)" },
  n4: { x: 218, y: 252, transform: "translate(-50%, -100%)" },
  n5: { x: 162, y: 332, transform: "translate(-50%, -100%)" },
  n6: { x: 218, y: 412, transform: "translate(-50%, -100%)" },
  n7: { x: 162, y: 492, transform: "translate(-50%, -100%)" },
  n8: { x: 218, y: 572, transform: "translate(-50%, -100%)" },
};

const flowTipEls = {};
for (const [id, pos] of Object.entries(FLOW_TIP_POS)) {
  const el = document.createElement("div");
  el.className = "flow-tip";
  el.style.left = (pos.x / 380) * 100 + "%";
  el.style.top = (pos.y / 640) * 100 + "%";
  el.style.transform = pos.transform;
  flowStage.appendChild(el);
  flowTipEls[id] = el;
}

const flow = { running: false, paused: false, seg: 0, phase: "travel", t: 0, raf: 0, last: 0 };
const flowPathLen = {};

function flowNode(id) {
  return document.getElementById(id);
}

function flowSetStatus(text, mode) {
  flowStatus.textContent = text;
  flowStatus.className = "flow-status " + mode;
}

function flowEase(t) {
  return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
}

function flowReset() {
  flow.running = false;
  flow.paused = false;
  flow.seg = 0; // 复位段索引：否则上一轮播完后重播会直接跳到「完成」
  flow.phase = "travel";
  flow.t = 0;
  flowPauseBtn.textContent = "暂停";
  flowPauseBtn.disabled = true;
  flowStage.classList.remove("paused");
  flowPanel.classList.remove("running");
  flowStage.querySelectorAll(".flow-node").forEach((n) => n.classList.remove("active", "done"));
  Object.values(flowTipEls).forEach((t) => t.classList.remove("show"));
  flowDot.setAttribute("opacity", "0");
  flowSetStatus("待机", "idle");
}

function flowStart() {
  if (flow.running) cancelAnimationFrame(flow.raf);
  flowReset();
  flow.running = true;
  flowNode("n1").classList.add("active");
  flowPanel.classList.add("running");
  flowSetStatus("演示中", "run");
  flowCaption.textContent = FLOW_SEGMENTS[0].cap;
  flowPauseBtn.disabled = false;
  flow.last = performance.now();
  flow.raf = requestAnimationFrame(flowLoop);
}

function flowLoop(ts) {
  const dt = Math.min(ts - flow.last, 120);
  flow.last = ts;
  if (!flow.paused) flowAdvance(dt);
  if (flow.running) flow.raf = requestAnimationFrame(flowLoop);
}

function flowAdvance(dt) {
  const seg = FLOW_SEGMENTS[flow.seg];
  if (!seg) {
    flowFinish();
    return;
  }
  const path = document.getElementById(seg.path);
  if (!path) return;
  if (!(seg.path in flowPathLen)) flowPathLen[seg.path] = path.getTotalLength();
  if (flow.phase === "travel") {
    flow.t += dt / FLOW_TRAVEL_MS;
    const p = Math.min(flow.t, 1);
    const pt = path.getPointAtLength(flowEase(p) * flowPathLen[seg.path]);
    flowDot.setAttribute("cx", pt.x);
    flowDot.setAttribute("cy", pt.y);
    flowDot.setAttribute("opacity", "1");
    flowDot.setAttribute("class", "flow-dot" + (seg.mode ? " " + seg.mode : ""));
    if (p >= 1) {
      flowNode(seg.from).classList.remove("active");
      flowNode(seg.from).classList.add("done");
      const target = flowNode(seg.to);
      target.classList.remove("done");
      target.classList.add("active");
      const tip = flowTipEls[seg.tip];
      tip.textContent = seg.label;
      tip.classList.add("show");
      flowCaption.textContent = seg.cap;
      flow.phase = "hold";
      flow.t = 0;
    }
  } else {
    flow.t += dt / FLOW_HOLD_MS;
    if (flow.t >= 1) {
      flowNode(seg.to).classList.remove("active");
      flowNode(seg.to).classList.add("done");
      flow.seg += 1;
      flow.phase = "travel";
      flow.t = 0;
      if (flow.seg >= FLOW_SEGMENTS.length) flowFinish();
    }
  }
}

function flowFinish() {
  flow.running = false;
  flowDot.setAttribute("opacity", "0");
  flowPanel.classList.remove("running");
  flowSetStatus("完成 ✓", "done");
  flowCaption.textContent = "演示完成：一次「问题 → 工具 → 数据 → 回答」的完整闭环（演示动画与真实请求异步）。";
  flowPauseBtn.disabled = true;
}

function flowTogglePause() {
  if (!flow.running) return;
  flow.paused = !flow.paused;
  flowStage.classList.toggle("paused", flow.paused);
  flowPauseBtn.textContent = flow.paused ? "继续" : "暂停";
  flowSetStatus(flow.paused ? "已暂停" : "演示中", flow.paused ? "paused" : "run");
}

/* ---------- 事件绑定 ---------- */

function onSend() {
  const question = questionInput.value.trim();
  if (!question) return;
  questionInput.value = "";
  flowStart();
  runAgent(question);
}

sendBtn.addEventListener("click", onSend);
questionInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") onSend();
});
healthBtn.addEventListener("click", healthCheck);
toolsBtn.addEventListener("click", listTools);
flowPauseBtn.addEventListener("click", flowTogglePause);
flowReplayBtn.addEventListener("click", flowStart);
