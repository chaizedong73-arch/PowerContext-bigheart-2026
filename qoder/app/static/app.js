// 复习教练 RecallLoop 前端逻辑：四页交互 + 无后端时的演示模式。
"use strict";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

let LIVE = false; // 后端是否可用

// ---------- 演示模式内置数据（无后端时使用） ----------
const DEMO = {
  documents: [
    { filename: "os-basics.md", chunks: 12, imported_at: Date.now() / 1000 | 0 },
    { filename: "study-notes.md", chunks: 8, imported_at: Date.now() / 1000 | 0 },
  ],
  ask: [
    { text: "seekdb 负责知识检索：导入资料切块后建向量索引，复习时做向量+关键词+元数据混合检索并给出处。", source: "os-basics.md", title: "两组件分工", score: 0.87, signals: { vector: 0.82, keyword: 0.94 } },
    { text: "PowerContext 负责学习者记忆：错题、薄弱点、偏好作为 Memory 长期保存，下次通过 context/prepare 取回。", source: "os-basics.md", title: "两组件分工", score: 0.81, signals: { vector: 0.79, keyword: 0.84 } },
    { text: "资料本身不入 PowerContext，避免与 seekdb 职责重叠。", source: "os-basics.md", title: "架构", score: 0.66, signals: { vector: 0.61, keyword: 0.73 } },
  ],
  cards: [
    { card_id: "d1", question: "关于《两组件分工》：seekdb 用＿＿＿索引资料？（来源：os-basics.md）", answer: "向量", source: "os-basics.md", title: "两组件分工", priority: "weak" },
    { card_id: "d2", question: "关于《架构》：PowerContext 通过 ＿＿＿ 端点取回历史上下文？（来源：os-basics.md）", answer: "context/prepare", source: "os-basics.md", title: "架构", priority: "normal" },
    { card_id: "d3", question: "关于《易混概念》：向量检索用＿＿＿相似度？（来源：study-notes.md）", answer: "cosine", source: "study-notes.md", title: "易混概念", priority: "normal" },
  ],
  plan: {
    plan: [
      { type: "weakness", source: "os-basics.md", reason: "PowerContext 记忆标记为薄弱点，优先复习", topics: ["两组件分工", "架构"] },
      { type: "review", source: "study-notes.md", title: "易混概念", reason: "上次答错，需巩固" },
    ],
    memory_context_status: "ready",
    memory_context: "上次复习：os-basics.md 两组件分工掌握模糊；薄弱点：向量索引、context/prepare。偏好：每天 20 分钟，先攻高频考点。",
    weak_points: [
      { text: "薄弱知识点：向量索引，来源 os-basics.md，第 1 次答错（不会）", score: 0.9 },
      { text: "薄弱知识点：context/prepare，来源 os-basics.md，第 1 次答错（模糊）", score: 0.8 },
    ],
    stats: { total_answers: 5, pass: 2, fuzzy: 1, fail: 2 },
  },
};

// ---------- 通用请求 ----------
async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

// ---------- 状态与 Tab ----------
async function checkHealth() {
  try {
    await api("/api/health");
    LIVE = true;
    $("#statusPill").textContent = "● 已连接后端";
    $("#statusPill").className = "status-pill ok";
    $("#demoBanner").classList.add("hidden");
  } catch (e) {
    LIVE = false;
    $("#statusPill").textContent = "● 演示模式";
    $("#statusPill").className = "status-pill demo";
    $("#demoBanner").classList.remove("hidden");
  }
}

function initTabs() {
  $$(".tab").forEach((tab) => {
    tab.addEventListener("click", () => activateTab(tab.dataset.tab));
  });
  $$("[data-goto]").forEach((b) => {
    b.addEventListener("click", () => activateTab(b.dataset.goto));
  });
}

function activateTab(name) {
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  $$(".panel").forEach((p) => p.classList.remove("active"));
  const panel = $("#panel-" + name);
  if (panel) panel.classList.add("active");
  if (name === "progress") loadProgress();
  if (name === "library") loadDocs();
  if (name === "ask") loadSourceOptions();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

// 首页「演示」区：用演示模式内置数据真实渲染三张界面快照
function renderAboutShots() {
  const ask = $("#shotAsk");
  if (ask) {
    ask.innerHTML = DEMO.ask.slice(0, 2).map((c, i) => `
      <div class="result-card">
        <p class="text">${escapeHtml(c.text)}</p>
        <div class="src"><span>#${i + 1}</span><span>出处：<strong>${escapeHtml(c.source)}</strong> / ${escapeHtml(c.title)}</span><span class="score">${c.score}</span></div>
      </div>`).join("");
  }
  const rev = $("#shotReview");
  if (rev) {
    const c = DEMO.cards[0];
    rev.innerHTML = `
      <p class="q">${escapeHtml(c.question)}</p>
      <div class="reveal" style="margin-bottom:10px"><strong>答案：</strong>${escapeHtml(c.answer)}<div class="src" style="font-size:11px;color:var(--muted);margin-top:5px">出处：${escapeHtml(c.source)} / ${escapeHtml(c.title)}</div></div>
      <div class="mini-stat">
        <div class="stat"><div class="n" style="color:var(--pass)">2</div><div class="l">会</div></div>
        <div class="stat"><div class="n" style="color:var(--fuzzy)">1</div><div class="l">模糊</div></div>
        <div class="stat"><div class="n" style="color:var(--fail)">2</div><div class="l">不会</div></div>
        <div class="stat"><div class="n">40%</div><div class="l">正确率</div></div>
      </div>`;
  }
  const prog = $("#shotProgress");
  if (prog) {
    const p = DEMO.plan;
    prog.innerHTML = `
      <div style="margin-bottom:8px"><span class="chip ${p.memory_context_status}">上次复习 ${p.memory_context_status}</span></div>
      <p class="ctx">${escapeHtml(p.memory_context)}</p>
      ${p.plan.map((it) => it.type === "weakness"
        ? `<div class="plan-li"><span>⚠️</span><div><strong style="font-size:12px">薄弱来源：${escapeHtml(it.source)}</strong><div class="doc-meta">${escapeHtml(it.reason)}</div></div></div>`
        : `<div class="plan-li"><span>📌</span><div><strong style="font-size:12px">${escapeHtml(it.title || it.source)}</strong><div class="doc-meta">${escapeHtml(it.source)} · ${escapeHtml(it.reason)}</div></div></div>`).join("")}`;
  }
}

function initTechTips() {
  const tip = $("#techTip");
  $$(".tech-badge").forEach((b) => {
    b.addEventListener("mouseenter", (e) => {
      tip.textContent = b.dataset.tech;
      tip.classList.remove("hidden");
      const r = b.getBoundingClientRect();
      tip.style.top = r.bottom + 8 + "px";
      tip.style.left = Math.min(r.left, window.innerWidth - 340) + "px";
    });
    b.addEventListener("mouseleave", () => tip.classList.add("hidden"));
  });
}

// ---------- 资料库 ----------
async function loadDocs() {
  const list = $("#docList");
  let docs;
  if (LIVE) {
    try { docs = (await api("/api/documents")).documents; }
    catch (e) { list.innerHTML = `<li class="empty">加载失败：${e.message}</li>`; return; }
  } else docs = DEMO.documents;
  if (!docs.length) { list.innerHTML = '<li class="empty">还没有导入资料</li>'; return; }
  list.innerHTML = docs.map((d) => `
    <li>
      <div><strong>${escapeHtml(d.filename)}</strong>
        <div class="doc-meta">${d.chunks} 块 · ${fmtTime(d.imported_at)}</div></div>
      <button class="ghost small" data-del="${escapeHtml(d.filename)}">删除</button>
    </li>`).join("");
  $$("[data-del]").forEach((b) => b.addEventListener("click", () => delDoc(b.dataset.del)));
  loadSourceOptions(docs);
}

async function delDoc(filename) {
  if (LIVE) { try { await api("/api/documents/" + encodeURIComponent(filename), { method: "DELETE" }); } catch (e) { alert(e.message); return; } }
  else DEMO.documents = DEMO.documents.filter((d) => d.filename !== filename);
  loadDocs();
}

async function importDoc() {
  const filename = $("#docName").value.trim();
  const content = $("#docContent").value;
  const msg = $("#importMsg");
  if (!filename) { msg.textContent = "请填写文件名"; msg.className = "msg err"; return; }
  if (!content.trim()) { msg.textContent = "请粘贴内容或选择文件"; msg.className = "msg err"; return; }
  $("#btnImport").disabled = true;
  try {
    if (LIVE) {
      const r = await api("/api/documents", { method: "POST", body: { filename, content } });
      msg.textContent = `已导入 ${r.chunks} 块到 ${r.collection}`;
    } else {
      DEMO.documents.push({ filename, chunks: Math.max(1, Math.ceil(content.length / 800)), imported_at: Date.now() / 1000 | 0 });
      msg.textContent = "（演示）已导入";
    }
    msg.className = "msg ok";
    $("#docContent").value = ""; $("#docName").value = "";
    loadDocs();
  } catch (e) { msg.textContent = "导入失败：" + e.message; msg.className = "msg err"; }
  finally { $("#btnImport").disabled = false; }
}

// ---------- 问答 ----------
async function loadSourceOptions(docs) {
  const sel = $("#askSource");
  let list = docs;
  if (!list) {
    if (LIVE) { try { list = (await api("/api/documents")).documents; } catch (e) { list = []; } }
    else list = DEMO.documents;
  }
  const cur = sel.value;
  sel.innerHTML = '<option value="">全部来源</option>' +
    list.map((d) => `<option value="${escapeHtml(d.filename)}">${escapeHtml(d.filename)}</option>`).join("");
  sel.value = cur;
}

async function doAsk() {
  const q = $("#askInput").value.trim();
  const box = $("#askResults");
  if (!q) return;
  const source = $("#askSource").value || undefined;
  box.innerHTML = '<div class="result-card"><p class="text">检索中…</p></div>';
  let chunks;
  if (LIVE) {
    try { chunks = (await api("/api/ask", { method: "POST", body: { question: q, source } })).chunks; }
    catch (e) { box.innerHTML = `<div class="result-card"><p class="text">检索失败：${escapeHtml(e.message)}</p></div>`; return; }
  } else {
    chunks = source ? DEMO.ask.filter((c) => c.source === source) : DEMO.ask;
  }
  if (!chunks.length) { box.innerHTML = '<div class="result-card"><p class="text">没有命中的资料块</p></div>'; return; }
  box.innerHTML = chunks.map((c, i) => `
    <div class="result-card">
      <p class="text">${escapeHtml(c.text)}</p>
      <div class="src">
        <span>#${i + 1}</span>
        <span>出处：<strong>${escapeHtml(c.source)}</strong> / ${escapeHtml(c.title || "")}</span>
        <span class="score">相关度 ${c.score}</span>
        <span class="sig">向量 ${c.signals?.vector ?? "-"} · 关键词 ${c.signals?.keyword ?? "-"}</span>
      </div>
    </div>`).join("");
}

// ---------- 复习 ----------
let cards = [], cardIdx = 0, tally = { pass: 0, fuzzy: 0, fail: 0 };

async function startReview() {
  const count = parseInt($("#cardCount").value || "5", 10);
  tally = { pass: 0, fuzzy: 0, fail: 0 }; cardIdx = 0;
  if (LIVE) {
    try { cards = (await api("/api/review/cards", { method: "POST", body: { count } })).cards; }
    catch (e) { alert("出题失败：" + e.message); return; }
  } else cards = DEMO.cards.slice(0, count);
  if (!cards.length) { alert("没有可用资料块，请先在资料库导入资料"); return; }
  $("#reviewStart").classList.add("hidden");
  $("#reviewDone").classList.add("hidden");
  $("#reviewCard").classList.remove("hidden");
  showCard();
}

function showCard() {
  const c = cards[cardIdx];
  $("#cardProgress").textContent = `第 ${cardIdx + 1} / ${cards.length} 张${c.priority === "weak" ? " · 薄弱点优先" : ""}`;
  $("#cardQuestion").textContent = c.question;
  $("#cardMeta").textContent = `来源：${c.source} / ${c.title || ""}`;
  $("#cardAnswer").textContent = c.answer;
  $("#cardSource").textContent = `出处：${c.source} / ${c.title || ""}`;
  $("#cardReveal").classList.add("hidden");
}

async function rate(result) {
  const c = cards[cardIdx];
  tally[result] = (tally[result] || 0) + 1;
  $("#cardReveal").classList.remove("hidden");
  if (LIVE) {
    try {
      await api("/api/review/answer", { method: "POST", body: {
        card_id: c.card_id, result, question: c.question, answer: c.answer,
        knowledge: c.knowledge || c.answer, source: c.source, title: c.title,
      }});
    } catch (e) { /* 记忆写入失败不阻断复习 */ }
  }
  setTimeout(() => {
    cardIdx++;
    if (cardIdx < cards.length) showCard();
    else finishReview();
  }, 700);
}

function finishReview() {
  $("#reviewCard").classList.add("hidden");
  $("#reviewDone").classList.remove("hidden");
  const total = cards.length;
  const acc = Math.round((tally.pass / total) * 100);
  $("#reviewStats").innerHTML = `
    <div class="stat-grid">
      <div class="stat"><div class="n">${total}</div><div class="l">卡片数</div></div>
      <div class="stat"><div class="n" style="color:var(--pass)">${tally.pass}</div><div class="l">会</div></div>
      <div class="stat"><div class="n" style="color:var(--fuzzy)">${tally.fuzzy}</div><div class="l">模糊</div></div>
      <div class="stat"><div class="n" style="color:var(--fail)">${tally.fail}</div><div class="l">不会</div></div>
      <div class="stat"><div class="n">${acc}%</div><div class="l">正确率</div></div>
    </div>
    <p class="hint">「不会/模糊」的知识点已作为薄弱点写入 PowerContext（演示模式下模拟）。</p>`;
}

function resetReview() {
  $("#reviewDone").classList.add("hidden");
  $("#reviewStart").classList.remove("hidden");
}

// ---------- 进度 ----------
async function loadProgress() {
  let data;
  if (LIVE) {
    try { data = await api("/api/plan"); }
    catch (e) { $("#lastContext").textContent = "加载失败：" + e.message; return; }
  } else data = DEMO.plan;

  const st = data.memory_context_status || "unavailable";
  const chip = $("#memStatus");
  chip.textContent = st;
  chip.className = "chip " + st;

  const ctx = $("#lastContext");
  if (st === "ready" && data.memory_context) ctx.textContent = data.memory_context;
  else if (st === "empty") ctx.textContent = "首次使用，还没有历史记录。";
  else ctx.textContent = "PowerContext Server 未连接。启动命令：\n  uv tool install --force \"powercontext[cli,server]==1.2.0\"\n  powercontext server run";

  const plan = $("#planList");
  plan.innerHTML = (data.plan || []).length
    ? data.plan.map((p) => p.type === "weakness"
        ? `<li><div><strong>薄弱来源：${escapeHtml(p.source)}</strong><div class="doc-meta">${escapeHtml(p.reason)}${p.topics?.length ? " · " + p.topics.map(escapeHtml).join("、") : ""}</div></div>⚠️</li>`
        : `<li><div><strong>${escapeHtml(p.title || p.source)}</strong><div class="doc-meta">${escapeHtml(p.source)} · ${escapeHtml(p.reason)}</div></div>📌</li>`).join("")
    : '<li class="empty">暂无推荐，先导入资料并复习</li>';

  const weak = $("#weakList");
  weak.innerHTML = (data.weak_points || []).length
    ? data.weak_points.map((w) => `<li><div>${escapeHtml(w.text)}</div><span class="score">${w.score}</span></li>`).join("")
    : '<li class="empty">暂无薄弱点记录</li>';
}

async function savePref() {
  const daily_minutes = parseInt($("#prefMinutes").value || "0", 10);
  const focus = $("#prefFocus").value.trim();
  const msg = $("#prefMsg");
  if (LIVE) {
    try { const r = await api("/api/preferences", { method: "POST", body: { daily_minutes, focus } });
      msg.textContent = r.saved ? "已保存偏好到 PowerContext" : ("保存失败：" + r.reason);
      msg.className = r.saved ? "msg ok" : "msg err";
    } catch (e) { msg.textContent = "保存失败：" + e.message; msg.className = "msg err"; }
  } else { msg.textContent = "（演示）偏好已模拟保存"; msg.className = "msg ok"; }
}

// ---------- 工具 ----------
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (m) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m]));
}
function fmtTime(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  return d.toLocaleString("zh-CN", { hour12: false });
}

// ---------- 初始化 ----------
function bindEvents() {
  $("#btnImport").addEventListener("click", importDoc);
  $("#btnRefreshDocs").addEventListener("click", loadDocs);
  $("#docFile").addEventListener("change", (e) => {
    const f = e.target.files[0];
    if (!f) return;
    if (!$("#docName").value) $("#docName").value = f.name;
    const reader = new FileReader();
    reader.onload = () => { $("#docContent").value = reader.result; };
    reader.readAsText(f);
  });
  $("#btnAsk").addEventListener("click", doAsk);
  $("#askInput").addEventListener("keydown", (e) => { if (e.key === "Enter") doAsk(); });
  $("#btnStartReview").addEventListener("click", startReview);
  $$(".rate").forEach((b) => b.addEventListener("click", () => rate(b.dataset.result)));
  $("#btnReviewAgain").addEventListener("click", resetReview);
  $("#btnSavePref").addEventListener("click", savePref);
}

(async function init() {
  initTabs();
  initTechTips();
  bindEvents();
  renderAboutShots();
  await checkHealth();
  loadDocs();
})();
