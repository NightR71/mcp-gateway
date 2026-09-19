/* /chat 简历助手前端（M4）：对话流 + 运行流程如实直播 + 维护态。
 *
 * 设计要点：
 * 1) 运行流程由**真实 SSE step 事件驱动**点亮（如实直播），不再是与请求无关的假动画；
 *    仅当流式异常（网络/非 2xx/中断）时降级播放预定义序列，保证流程图仍有内容。
 * 2) 能力探测：启动时 GET /agent/status；usable=false（无 Key / 模型不可用 / mock 未放行）
 *    则显示维护横幅并禁用提问——绝不用假剧本冒充回答（执行计划 §2 决策 6）。
 * 3) 气泡卡问题由服务端配置下发（preset_questions），前端不硬编码业务内容。
 * 4) 复用旧 /ui 的富文本渲染与 SSE 分帧解析思路（本文件独立实现，双向无依赖）。
 */

const KEY_STORAGE = "mcp_chat_key";
const VISITOR_KEY =
  (document.querySelector('meta[name="visitor-key"]') || {}).content || "";

const streamEl = document.getElementById("stream");
const questionInput = document.getElementById("question");
const sendBtn = document.getElementById("send-btn");
const bubblesEl = document.getElementById("bubbles");
const maintenanceEl = document.getElementById("maintenance");
const maintenanceText = document.getElementById("maintenance-text");
const flowToggle = document.getElementById("flow-toggle");
const flowBack = document.getElementById("flow-back");
const flowStatus = document.getElementById("flow-status");
const flowCaption = document.getElementById("flow-caption-text");
const flowPanel = document.getElementById("side-flow");
const defaultPanel = document.getElementById("side-default");

let busy = false;
let agentUsable = false;

/* ---------------- 富文本渲染（Markdown 子集 → HTML） ---------------- */

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function renderRich(container, text) {
  const lines = String(text == null ? "" : text).replace(/\r\n/g, "\n").split("\n");
  const isFence = (s) => /^\s*```/.test(s);
  const isTableRow = (s) => /^\s*\|.*\|\s*$/.test(s);
  const isBullet = (s) => /^\s*[-*]\s+/.test(s);
  const isHeading = (s) => /^#{1,4}\s+/.test(s);
  const cells = (row) =>
    row.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());

  let html = "";
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (isFence(line)) {
      const lang = line.trim().replace(/^```/, "").trim();
      i += 1;
      const code = [];
      while (i < lines.length && !isFence(lines[i])) code.push(lines[i++]);
      i += 1;
      html +=
        '<pre class="code-block">' +
        (lang ? `<span class="code-lang">${escapeHtml(lang)}</span>` : "") +
        `<code>${escapeHtml(code.join("\n"))}</code></pre>`;
      continue;
    }
    if (isTableRow(line)) {
      const rows = [];
      while (i < lines.length && isTableRow(lines[i])) rows.push(lines[i++]);
      const isSep = (row) => cells(row).every((c) => /^:?-{2,}:?$/.test(c));
      const head = cells(rows[0]);
      const body = rows.slice(1).filter((r) => !isSep(r)).map(cells);
      html +=
        "<table class=\"md-table\"><thead><tr>" +
        head.map((c) => `<th>${escapeHtml(c)}</th>`).join("") +
        "</tr></thead><tbody>" +
        body.map((r) => `<tr>${r.map((c) => `<td>${escapeHtml(c)}</td>`).join("")}</tr>`).join("") +
        "</tbody></table>";
      continue;
    }
    if (isBullet(line)) {
      const items = [];
      while (i < lines.length && isBullet(lines[i])) {
        items.push(lines[i++].replace(/^\s*[-*]\s+/, ""));
      }
      html += `<ul>${items.map((t) => `<li>${inline(t)}</li>`).join("")}</ul>`;
      continue;
    }
    if (isHeading(line)) {
      const level = line.match(/^#+/)[0].length;
      const text2 = line.replace(/^#{1,4}\s+/, "");
      html += `<p><strong>${inline(text2)}</strong></p>`;
      i += 1;
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
      !isBullet(lines[i]) &&
      !isHeading(lines[i])
    ) {
      para.push(lines[i++]);
    }
    html += `<p>${para.map(inline).join("<br>")}</p>`;
  }
  container.innerHTML = html;
}

/* 行内标记：转义后仅放行 **粗体** 与 `代码` */
function inline(text) {
  return escapeHtml(text)
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
}

/* ---------------- 对话流 ---------------- */

function scrollBottom() {
  streamEl.scrollTop = streamEl.scrollHeight;
}

function addUserMessage(text) {
  const el = document.createElement("div");
  el.className = "msg msg-user";
  el.innerHTML = '<span class="label">面试官</span>';
  const body = document.createElement("div");
  body.textContent = text;
  el.appendChild(body);
  streamEl.appendChild(el);
  scrollBottom();
  return el;
}

const STEP_LABEL = {
  tool_select: "工具选择",
  tool_call: "网关执行",
  tool_result: "工具结果",
  final: "回答",
};

function addStepCard(text, opts = {}) {
  const el = document.createElement("div");
  el.className = "step" + (opts.isError ? " is-error" : "");
  const head = document.createElement("div");
  head.className = "head";
  const badge = document.createElement("span");
  badge.className = "badge";
  badge.textContent = opts.badge || "网关";
  head.appendChild(badge);
  const label = document.createElement("span");
  label.textContent = opts.label || "";
  head.appendChild(label);
  if (typeof opts.latencyMs === "number") {
    const ms = document.createElement("span");
    ms.className = "ms";
    ms.textContent = Math.round(opts.latencyMs) + " ms";
    head.appendChild(ms);
  }
  el.appendChild(head);
  if (text) {
    const body = document.createElement("div");
    body.className = "rich";
    renderRich(body, text);
    el.appendChild(body);
  }
  streamEl.appendChild(el);
  scrollBottom();
  return el;
}

function addAnswerCard() {
  const el = document.createElement("div");
  el.className = "msg msg-answer";
  el.innerHTML = '<span class="label">技术代言人</span>';
  const body = document.createElement("div");
  body.className = "rich cursor";
  el.appendChild(body);
  streamEl.appendChild(el);
  scrollBottom();
  return el;
}

/* ---------------- 运行流程图：段定义与动画引擎 ---------------- */

/* 每段：路径 id + 到达后点亮的节点 + 字幕。tool_call 一次触发多段（模型决策 →
 * Registry 路由 → MCP 调用 → Server 执行），因为这些环节在真实请求里确实连续发生，
 * 只是没有各自独立的 SSE 事件。 */
const SEGMENTS = {
  enter: { path: "fp12", to: "n2", cap: "提问进入网关：校验访客 Key（失败 401）→ 令牌桶限流（超限 429）" },
  route: { path: "fp23", to: "n3", cap: "ToolRouter 对问题分词打分，只注入相关工具，控制上下文预算" },
  decide: { path: "fp34", to: "n4", cap: "Agent 模型决定调用哪个知识库工具" },
  registry: { path: "fp45", to: "n5", cap: "Registry 按 {server}__{tool} 命名空间路由到来源 Server" },
  call: { path: "fp56", to: "n6", cap: "MCP Client 经 inprocess 传输发出协议调用（总超时保护）" },
  exec: { path: "fp67", to: "n7", cap: "resume_kb 在只读知识库里做 2-gram 检索" },
  result: { path: "fp78", to: "n8", cap: "命中知识卡片，内容沿链路返回" },
  backfill: { path: "fpRet", to: "n4", cap: "工具结果回填模型上下文" },
  answer: { path: "fpAns", to: "n1", cap: "SSE 逐字流式输出回答；输出守门逐段扫描 PII 并打码" },
};

const FALLBACK_SEQUENCE = [
  "enter", "route", "decide", "registry", "call", "exec", "result", "backfill", "answer",
];

const flowDot = document.getElementById("flow-dot");
const flow = { queue: [], playing: false, raf: 0, timer: 0, t: 0, seg: null, last: 0, settled: false };
const pathLenCache = {};
const TRAVEL_MS = 620;

function pathLen(id) {
  const el = document.getElementById(id);
  if (!el) return 0;
  if (!(id in pathLenCache)) pathLenCache[id] = el.getTotalLength();
  return pathLenCache[id];
}

function setFlowStatus(text, mode) {
  flowStatus.textContent = text;
  flowStatus.className = "flow-status " + mode;
}

function lightNode(id) {
  const node = document.getElementById(id);
  if (node) node.classList.add("done");
}

function flowReset() {
  flow.queue = [];
  flow.playing = false;
  flow.seg = null;
  flow.t = 0;
  flow.fallback = false;
  flow.settled = false;
  cancelAnimationFrame(flow.raf);
  clearTimeout(flow.timer);
  flowDot.setAttribute("opacity", "0");
  document.querySelectorAll("#flow-stage .flow-node").forEach((n) => n.classList.remove("active", "done"));
  document.querySelectorAll("#flow-stage .flow-link").forEach((p) => p.classList.remove("lit"));
  setFlowStatus("待机", "idle");
  flowCaption.textContent = "发送问题后，节点会随真实请求逐步点亮。";
}

function enqueue(...names) {
  flow.queue.push(...names);
  if (!flow.playing) playNext();
}

/* 播放一段：**立即**点亮目标节点（不依赖动画帧），光点平滑移动只是视觉增强，
 * 队列推进由 setTimeout 兜底——rAF 在后台标签页会被暂停，若把状态推进压在 rAF 上，
 * 流程图会永远卡在第一段（节点点亮必须可靠，动画可以降级）。 */
function playNext() {
  const name = flow.queue.shift();
  if (!name) {
    flow.playing = false;
    flow.seg = null;
    if (flow.settled) setFlowStatus("完成", "done");
    return;
  }
  const seg = SEGMENTS[name];
  if (!seg) {
    playNext();
    return;
  }
  flow.playing = true;
  flow.seg = seg;
  flow.t = 0;
  flow.last = performance.now();
  flowCaption.textContent = seg.cap;
  document.querySelectorAll("#flow-stage .flow-node").forEach((n) => n.classList.remove("active"));
  const node = document.getElementById(seg.to);
  if (node) node.classList.add("active");
  const link = document.getElementById(seg.path);
  if (link) link.classList.add("lit");
  flowDot.setAttribute("opacity", "1");

  cancelAnimationFrame(flow.raf);
  flow.raf = requestAnimationFrame(tick); // 光点位置（可被 rAF 节流影响）
  clearTimeout(flow.timer);
  flow.timer = setTimeout(finishSegment, TRAVEL_MS + 40); // 队列推进（不依赖 rAF）
}

function finishSegment() {
  if (!flow.seg) return;
  cancelAnimationFrame(flow.raf);
  lightNode(flow.seg.to);
  flowDot.setAttribute("opacity", "0");
  const hasNext = flow.queue.length > 0;
  flow.seg = null;
  if (hasNext) flowCaption.textContent = SEGMENTS[flow.queue[0]] ? SEGMENTS[flow.queue[0]].cap : "";
  playNext();
}

/* 光点沿路径移动（纯视觉）；推进由 finishSegment 负责 */
function tick(ts) {
  const dt = Math.min(ts - flow.last, 120);
  flow.last = ts;
  if (!flow.seg) return;
  flow.t += dt;
  const progress = Math.min(1, flow.t / TRAVEL_MS);
  const eased = progress < 0.5 ? 4 * progress ** 3 : 1 - (-2 * progress + 2) ** 3 / 2;
  const path = document.getElementById(flow.seg.path);
  if (path) {
    const point = path.getPointAtLength(pathLen(flow.seg.path) * eased);
    flowDot.setAttribute("cx", point.x);
    flowDot.setAttribute("cy", point.y);
  }
  if (progress < 1) flow.raf = requestAnimationFrame(tick);
}

/* 降级：流式异常时按预定义序列播放（明确标注为降级，不冒充真实链路） */
function playFallbackFlow() {
  flow.fallback = true;
  setFlowStatus("降级演示", "idle");
  flowCaption.textContent = "流式连接异常，以下为链路示意（非本次真实时序）。";
  enqueue(...FALLBACK_SEQUENCE);
}

/* ---------------- 面板切换（移动端标签 / 桌面常驻） ---------------- */

function showFlowPanel() {
  flowPanel.classList.remove("hidden");
  defaultPanel.classList.add("hidden");
  if (flowToggle) flowToggle.classList.add("active");
}

function showDefaultPanel() {
  flowPanel.classList.add("hidden");
  defaultPanel.classList.remove("hidden");
  if (flowToggle) flowToggle.classList.remove("active");
  document.body.classList.remove("show-flow");
}

/* ---------------- 请求与 SSE ---------------- */

function apiKey() {
  return localStorage.getItem(KEY_STORAGE) || VISITOR_KEY;
}

/* 失败兜底（M6）：把技术性失败翻译成"能怎么办"的提示。
 * 背景（M5 §7.5 实测）：连接重试 10 次后仍有约 1% 的单问失败概率，
 * 属于可重试的偶发——面试官看到的应该是「请重试」，而不是光秃秃的错误码。 */
function withRetryHint(message) {
  return `网络波动或服务暂时不稳定，请重试一次。${message ? `（${message}）` : ""}`;
}

function failureHint(status) {
  if (status === 401) return "演示 Key 无效或已更换，请与本人联系。";
  if (status === 429) return "访问过于频繁（访客额度 50 次/小时），请稍后再试。";
  if (status === 503) return "AI 答疑当前不可用（维护中），请稍后再来，或先查看简历页。";
  if (status === 502 || status === 504) return withRetryHint(`服务返回 HTTP ${status}`);
  if (status >= 500) return withRetryHint(`服务返回 HTTP ${status}`);
  return `无法建立连接（HTTP ${status}）。`;
}

async function sendQuestion(question) {
  if (busy || !question.trim()) return;
  if (!agentUsable) {
    addStepCard("AI 答疑当前不可用，请稍后再试。", { isError: true, badge: "维护" });
    return;
  }
  busy = true;
  sendBtn.disabled = true;
  questionInput.value = "";
  addUserMessage(question);
  showFlowPanel();
  flowReset();
  setFlowStatus("运行中", "run");

  let answerCard = null;
  try {
    answerCard = await streamAgent(question);
    // 真实事件已收完；流程图队列播完后由 playNext 置「完成」，再停留数秒恢复默认态
    flow.settled = true;
    if (!flow.playing && flow.queue.length === 0) setFlowStatus("完成", "done");
    scheduleReturnToDefault();
  } catch (err) {
    setFlowStatus("异常", "error");
    // 网络层直接失败（fetch reject）时 message 形如 "Failed to fetch"——不是给面试官看的
    const raw = String((err && err.message) || err);
    const message = /failed to fetch|networkerror|load failed|中断/i.test(raw)
      ? withRetryHint("连接中断")
      : raw;
    addStepCard(message, { isError: true, badge: "错误" });
    playFallbackFlow();
  } finally {
    if (answerCard) {
      const body = answerCard.querySelector(".body, .rich");
      if (body) body.classList.remove("cursor");
    }
    busy = false;
    sendBtn.disabled = false;
  }
}

/* 流程图播完后再停留数秒，然后自动回到默认态（建议问题 + 简历卡）。
 * 若期间用户又提问（busy）则不切换。 */
let returnTimer = 0;
function scheduleReturnToDefault() {
  clearTimeout(returnTimer);
  const waitFlow = () => {
    if (flow.playing || flow.queue.length) {
      returnTimer = setTimeout(waitFlow, 300);
      return;
    }
    returnTimer = setTimeout(() => {
      if (!busy) showDefaultPanel();
    }, 5000);
  };
  waitFlow();
}

async function streamAgent(question) {
  const resp = await fetch("/agent/run/stream", {
    method: "POST",
    headers: { "X-API-Key": apiKey(), "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
  if (!resp.ok || !resp.body) {
    const payload = await resp.json().catch(() => ({}));
    const detail = payload.detail ? `：${payload.detail}` : "";
    throw new Error(`${failureHint(resp.status)}（HTTP ${resp.status}${detail}）`);
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let answerCard = null;
  let sawError = false;

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let sep;
    while ((sep = buffer.indexOf("\n\n")) >= 0) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      const result = handleFrame(frame, answerCard);
      if (result && result.card) answerCard = result.card;
      if (result && result.error) sawError = true;
    }
  }
  if (!answerCard && !sawError) {
    throw new Error(withRetryHint("流式连接中断，未收到回答"));
  }
  return answerCard;
}

/* 解析单个 SSE 帧；返回 {card} 或 {error:true} */
function handleFrame(frame, answerCard) {
  let eventName = null;
  const dataLines = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith("event: ")) eventName = line.slice(7).trim();
    else if (line.startsWith("data: ")) dataLines.push(line.slice(6));
  }
  if (!eventName || dataLines.length === 0) return null;
  let payload;
  try {
    payload = JSON.parse(dataLines.join("\n"));
  } catch {
    return null;
  }

  if (eventName === "step") return { card: renderStep(payload.step, answerCard) };

  if (eventName === "token" && payload.text) {
    let card = answerCard;
    if (!card) card = addAnswerCard();
    card.querySelector(".rich").textContent += payload.text;
    scrollBottom();
    return { card };
  }

  if (eventName === "error") {
    // M6 失败兜底：服务端给的是不透明文案（不泄露内部细节），前端补一句可操作的话；
    // 关联 ID 保留给用户回报问题用（M1 的 log_and_hide 会带上它）。
    const detail = payload.detail || payload.error || "";
    const idMatch = /关联ID[:：]\s*([A-Za-z0-9]+)/.exec(detail);
    addStepCard(
      idMatch
        ? `${withRetryHint(`回答中断，关联ID ${idMatch[1]}`)}`
        : `回答中断：${detail || "网络波动或服务暂时不稳定"}，请重试一次。`,
      { isError: true, badge: "错误" },
    );
    return { error: true };
  }

  if (eventName === "done" && payload.response) {
    const resp = payload.response;
    if (resp.extra && resp.extra.policy) {
      addStepCard("命中红线话题，已用固定话术回答（未调用模型）。", { badge: "规则" });
    }
    return null;
  }
  return null;
}

/* 步骤 → 对话卡 + 流程图动作（如实映射） */
function renderStep(step, answerCard) {
  if (!step) return null;

  if (step.kind === "user") {
    enqueue("enter");
    return null;
  }

  if (step.kind === "tool_select") {
    enqueue("route");
    addStepCard(step.content, { badge: "路由", label: "语义筛选" });
    return null;
  }

  if (step.kind === "tool_call") {
    enqueue("decide", "registry", "call", "exec");
    addStepCard(
      step.is_error ? step.content || "工具调用失败" : `调用 ${step.tool}`,
      { badge: "网关", label: step.is_error ? "失败" : "鉴权 ✓ 限流 ✓", latencyMs: step.latency_ms, isError: step.is_error }
    );
    return null;
  }

  if (step.kind === "tool_result") {
    enqueue("result");
    const card = addStepCard("", { badge: "工具", label: `${step.tool} 返回`, latencyMs: step.latency_ms, isError: step.is_error });
    const body = document.createElement("div");
    body.className = "rich";
    renderRich(body, step.content);
    card.appendChild(body);
    return null;
  }

  if (step.kind === "final") {
    enqueue("backfill", "answer");
    let card = answerCard;
    if (!card) card = addAnswerCard();
    const body = card.querySelector(".rich");
    body.classList.remove("cursor");
    if (step.is_error) card.classList.add("is-error");
    renderRich(body, step.content || "");
    scrollBottom();
    return card;
  }

  enqueue();
  addStepCard(step.content, { badge: step.kind, isError: step.is_error, latencyMs: step.latency_ms });
  return null;
}

/* ---------------- 启动：能力探测 + 气泡卡 ---------------- */

function renderBubbles(questions) {
  bubblesEl.innerHTML = "";
  for (const question of questions) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "bubble";
    btn.textContent = question;
    btn.addEventListener("click", () => sendQuestion(question));
    bubblesEl.appendChild(btn);
  }
}

function applyMaintenance(text) {
  agentUsable = false;
  maintenanceText.textContent = text;
  maintenanceEl.classList.remove("hidden");
  questionInput.disabled = true;
  sendBtn.disabled = true;
  questionInput.placeholder = "AI 答疑正在准备中";
  document.querySelectorAll(".bubble").forEach((b) => (b.disabled = true));
}

async function bootstrap() {
  renderBubbles([]);
  let status = null;
  try {
    const resp = await fetch("/agent/status");
    if (resp.ok) status = await resp.json();
  } catch {
    status = null;
  }

  if (!status) {
    applyMaintenance("无法连接服务，请稍后再试。");
    return;
  }

  renderBubbles(status.preset_questions || []);
  if (status.usable) {
    agentUsable = true;
    maintenanceEl.classList.add("hidden");
  } else {
    const hint =
      status.mode === "mock"
        ? "AI 答疑尚未开放（当前未接入正式模型）。你可以先查看简历与项目介绍。"
        : status.maintenance_hint || "AI 答疑正在准备中。";
    applyMaintenance(hint);
  }
}

sendBtn.addEventListener("click", () => sendQuestion(questionInput.value));
questionInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.isComposing) {
    event.preventDefault();
    sendQuestion(questionInput.value);
  }
});

if (flowToggle) {
  flowToggle.addEventListener("click", () => {
    const showing = !flowPanel.classList.contains("hidden");
    if (showing) {
      showDefaultPanel();
    } else {
      document.body.classList.add("show-flow");
      showFlowPanel();
    }
  });
}
if (flowBack) flowBack.addEventListener("click", showDefaultPanel);

bootstrap();
