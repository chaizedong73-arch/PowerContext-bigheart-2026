// 端侧推理前端：Transformers.js（ONNX Runtime Web）在浏览器本地完成 embedding，
// WebGPU 优先，自动降级 WASM；向量与文本写入 seekdb，混合检索由 seekdb 完成。
// 全部资源本地自托管（public/vendor/）：transformers.js + ONNX Runtime Web wasm + 模型权重，
// 运行时不依赖任何外网 CDN，适配离线/内网演示环境。获取模型见 scripts/download-models.sh。
import { pipeline, env } from "./vendor/transformers/transformers.min.js";

const MODEL_ID = "Xenova/all-MiniLM-L6-v2";
const DIM = 384;

env.allowRemoteModels = false;
env.allowLocalModels = true;
env.localModelPath = new URL("vendor/models/", import.meta.url).href;
env.backends.onnx.wasm.wasmPaths = new URL("vendor/transformers/", import.meta.url).href;

const $ = (id) => document.getElementById(id);
const els = {
  backendBadge: $("backend-badge"), dtypeBadge: $("dtype-badge"),
  seekdbBadge: $("seekdb-badge"), modelBadge: $("model-badge"),
  device: $("device-select"), dtype: $("dtype-select"),
  load: $("btn-load"), modelProgress: $("model-progress"), modelStatus: $("model-status"),
  collectionSelect: $("collection-select"), newCollection: $("new-collection-name"),
  createCollection: $("btn-create-collection"), deleteCollection: $("btn-delete-collection"),
  refreshCollections: $("btn-refresh-collections"), collectionStatus: $("collection-status"),
  importText: $("import-text"), importFile: $("import-file"), importSample: $("btn-import-sample"),
  importBtn: $("btn-import"), batchSize: $("batch-size"),
  importProgress: $("import-progress"), importStatus: $("import-status"),
  query: $("query-input"), topk: $("topk-input"), search: $("btn-search"),
  searchTiming: $("search-timing"), results: $("results"),
  perfTable: $("perf-table").querySelector("tbody"),
  exportPerf: $("btn-export-perf"), clearPerf: $("btn-clear-perf"),
};

let extractor = null;
let modelInfo = { device: null, dtype: null };
let currentCollection = localStorage.getItem("kb.currentCollection") || "";
const perfRecords = JSON.parse(localStorage.getItem("kb.perf") || "[]");

function setBadge(el, text, cls) { el.textContent = text; el.className = "badge" + (cls ? " " + cls : ""); }
function setStatus(el, text, cls) { el.textContent = text; el.className = "status" + (cls ? " " + cls : ""); }
function fmt(ms) { return ms >= 100 ? Math.round(ms) + " ms" : ms.toFixed(1) + " ms"; }

function recordPerf(phase, ms, scale = "") {
  perfRecords.push({ ts: new Date().toLocaleTimeString(), device: modelInfo.device || "-", dtype: modelInfo.dtype || "-", phase, ms: Math.round(ms * 100) / 100, scale });
  localStorage.setItem("kb.perf", JSON.stringify(perfRecords.slice(-500)));
  renderPerf();
}

function renderPerf() {
  els.perfTable.innerHTML = perfRecords.slice(-50).reverse().map((r) =>
    `<tr><td>${r.ts}</td><td>${r.device}</td><td>${r.dtype}</td><td>${r.phase}</td><td>${r.ms}</td><td>${r.scale || "-"}</td></tr>`
  ).join("");
}

export function median(xs) {
  if (!xs.length) return null;
  const s = [...xs].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

// ---------- 后端检测与模型加载 ----------

async function detectWebGPU() {
  if (!navigator.gpu) return false;
  try {
    // requestAdapter 在部分环境（无头/软件渲染）可能永久挂起，3 秒超时视为不可用
    const adapter = await Promise.race([
      navigator.gpu.requestAdapter(),
      new Promise((resolve) => setTimeout(() => resolve(null), 3000)),
    ]);
    return !!adapter;
  } catch { return false; }
}

// WebGPU 优先；不可用或初始化失败时自动降级 WASM，并在界面明确提示。
async function loadModel() {
  els.load.disabled = true;
  extractor = null;
  let wantDevice = els.device.value;
  let wantDtype = els.dtype.value;
  const hasWebGPU = await detectWebGPU();
  if (wantDevice === "auto") wantDevice = hasWebGPU ? "webgpu" : "wasm";
  if (wantDevice === "webgpu" && !hasWebGPU) {
    setStatus(els.modelStatus, "WebGPU 不可用，自动降级到 WASM", "warn");
    wantDevice = "wasm";
  }
  if (wantDtype === "auto") wantDtype = wantDevice === "webgpu" ? "fp16" : "q8";

  const tryLoad = async (device, dtype) => {
    const t0 = performance.now();
    let totalBytes = 0;
    const files = new Set();
    const pipePromise = pipeline("feature-extraction", MODEL_ID, {
      device, dtype,
      progress_callback: (p) => {
        if (p.status === "progress" && p.total) {
          files.add(p.file); totalBytes += p.total; // 记录一次即可，progress 事件会重复
        }
        if (p.status === "progress") {
          els.modelProgress.style.width = Math.min(100, p.progress || 0) + "%";
          els.modelStatus.textContent = `下载 ${p.file}: ${(p.loaded / 1e6).toFixed(1)} / ${(p.total / 1e6).toFixed(1)} MB`;
        } else if (p.status === "initiate") {
          els.modelStatus.textContent = `开始获取 ${p.file} …`;
        } else if (p.status === "ready") {
          els.modelStatus.textContent = "模型就绪，初始化推理会话…";
        }
      },
    });
    // 管线初始化可能挂起（如 WebGPU 后端异常），90 秒超时触发降级
    const pipe = await Promise.race([
      pipePromise,
      new Promise((_, reject) => setTimeout(() => reject(new Error("pipeline init timeout (90s)")), 90000)),
    ]);
    const loadMs = performance.now() - t0;
    return { pipe, loadMs, files: [...files] };
  };

  try {
    const { pipe, loadMs, files } = await tryLoad(wantDevice, wantDtype);
    extractor = pipe;
    modelInfo = { device: wantDevice, dtype: wantDtype };
    setBadge(els.backendBadge, `后端：${wantDevice.toUpperCase()}`, "ok");
    setBadge(els.dtypeBadge, `精度：${wantDtype}`, "ok");
    setBadge(els.modelBadge, `模型：${MODEL_ID.split("/")[1]}（${wantDtype}）`, "ok");
    setStatus(els.modelStatus, `加载完成：device=${wantDevice}, dtype=${wantDtype}, 首次加载（含下载）${fmt(loadMs)}，涉及 ${files.length} 个文件。二次加载命中浏览器缓存将显著加快。`, "ok");
    recordPerf("模型下载+加载(首次含网络)", loadMs, files.join(", "));
  } catch (err) {
    if (wantDevice === "webgpu") {
      setStatus(els.modelStatus, `WebGPU 初始化失败（${err.message}），自动降级到 WASM…`, "warn");
      try {
        const { pipe, loadMs, files } = await tryLoad("wasm", wantDtype === "fp16" ? "q8" : wantDtype);
        extractor = pipe;
        modelInfo = { device: "wasm", dtype: wantDtype === "fp16" ? "q8" : wantDtype };
        setBadge(els.backendBadge, "后端：WASM（WebGPU 降级）", "warn");
        setBadge(els.dtypeBadge, `精度：${modelInfo.dtype}`, "ok");
        setBadge(els.modelBadge, `模型：${MODEL_ID.split("/")[1]}（${modelInfo.dtype}）`, "ok");
        setStatus(els.modelStatus, `WASM 加载完成：首次加载 ${fmt(loadMs)}。`, "ok");
        recordPerf("模型下载+加载(WASM降级)", loadMs, files.join(", "));
      } catch (err2) {
        setStatus(els.modelStatus, "模型加载失败：" + err2.message, "error");
      }
    } else {
      setStatus(els.modelStatus, "模型加载失败：" + err.message, "error");
    }
  } finally {
    els.load.disabled = false;
    els.modelProgress.style.width = "0%";
  }
}

// ---------- 本地向量化 ----------

async function embedTexts(texts, batchSize = 32, onProgress = null) {
  if (!extractor) throw new Error("模型未加载");
  const vectors = [];
  const t0 = performance.now();
  for (let i = 0; i < texts.length; i += batchSize) {
    const batch = texts.slice(i, i + batchSize);
    const tb = performance.now();
    const output = await extractor(batch, { pooling: "mean", normalize: true });
    const batchMs = performance.now() - tb;
    for (const v of output.tolist()) vectors.push(v);
    if (onProgress) onProgress(Math.min(texts.length, i + batchSize), texts.length, batchMs, batch.length);
  }
  return { vectors, totalMs: performance.now() - t0 };
}

// ---------- 集合管理 ----------

async function refreshCollections(keep = currentCollection) {
  const res = await fetch("/api/collections");
  const data = await res.json();
  const list = data.collections || [];
  els.collectionSelect.innerHTML = list.length
    ? list.map((c) => `<option value="${c.name}">${c.name}（${c.dimension ?? "?"} 维）</option>`).join("")
    : `<option value="">（暂无集合）</option>`;
  if (keep && list.some((c) => c.name === keep)) {
    els.collectionSelect.value = keep;
    currentCollection = keep;
  } else {
    currentCollection = list[0]?.name || "";
    if (currentCollection) els.collectionSelect.value = currentCollection;
  }
  localStorage.setItem("kb.currentCollection", currentCollection);
  await updateCollectionStatus();
  return list;
}

async function updateCollectionStatus() {
  if (!currentCollection) { setStatus(els.collectionStatus, "尚未选择集合。创建集合或导入语料时会自动创建。"); return; }
  setStatus(els.collectionStatus, `当前集合：${currentCollection}（会话状态已保存，刷新页面后自动恢复）`, "ok");
}

async function createCollection() {
  const name = els.newCollection.value.trim();
  if (!name) return setStatus(els.collectionStatus, "请输入集合名称", "error");
  await fetch("/api/collections", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, dimension: DIM }) });
  els.newCollection.value = "";
  await refreshCollections(name);
}

async function deleteCurrentCollection() {
  if (!currentCollection) return;
  if (!confirm(`确认删除集合 ${currentCollection} 及其全部数据？`)) return;
  await fetch(`/api/collections/${encodeURIComponent(currentCollection)}`, { method: "DELETE" });
  currentCollection = "";
  localStorage.setItem("kb.currentCollection", "");
  await refreshCollections();
}

// ---------- 语料导入 ----------

function parseImportText(raw) {
  const trimmed = raw.trim();
  if (!trimmed) return [];
  if (els.importFile.value.endsWith(".jsonl") || trimmed.startsWith("{")) {
    // 每行 {"title": "...", "text": "..."}
    return trimmed.split("\n").filter((l) => l.trim()).map((l) => {
      const obj = JSON.parse(l);
      return { title: obj.title || obj.title || "", text: obj.text || obj.content || String(obj) };
    });
  }
  return trimmed.split(/\n\s*\n/).map((t) => ({ title: t.split("\n")[0].slice(0, 40), text: t.trim() })).filter((d) => d.text);
}

async function importDocs() {
  if (!extractor) return setStatus(els.importStatus, "请先加载模型", "error");
  let docs = parseImportText(els.importText.value);
  if (els.importFile.files[0]) {
    const text = await els.importFile.files[0].text();
    const parsed = parseImportText(text);
    if (parsed.length) docs = parsed;
  }
  if (!docs.length) return setStatus(els.importStatus, "没有可导入的文本", "error");
  if (!currentCollection) {
    els.newCollection.value = els.newCollection.value.trim() || "my_knowledge_base";
    await createCollection(); // 未选集合时自动创建默认集合
    if (!currentCollection) return setStatus(els.importStatus, "请先创建或选择集合", "error");
  }
  els.importBtn.disabled = true;
  try {
    const t0 = performance.now();
    setStatus(els.importStatus, `开始向量化 ${docs.length} 个文档（本地推理）…`);
    const batchSize = Number(els.batchSize.value) || 32;
    const { vectors, totalMs } = await embedTexts(docs.map((d) => d.text), batchSize,
      (done, total, batchMs, n) => {
        els.importProgress.style.width = (done / total * 100) + "%";
        setStatus(els.importStatus, `向量化进度 ${done}/${total}（本批 ${n} 条 ${fmt(batchMs)}）`);
      });
    const embedMs = performance.now() - t0;
    recordPerf("文档向量化", embedMs, `${docs.length} docs / ${batchSize} batch`);
    const throughput = docs.length / (embedMs / 1000);

    setStatus(els.importStatus, "写入 seekdb 并等待索引刷新…");
    const now = new Date().toISOString();
    const records = docs.map((d, i) => ({
      id: `doc-${String(i + 1).padStart(4, "0")}`, // 稳定 ID：重复导入幂等（upsert）
      text: d.text,
      embedding: vectors[i],
      metadata: { title: d.title, index: i + 1, source: els.importFile.files[0]?.name || "paste", importedAt: now },
    }));
    const t1 = performance.now();
    const res = await fetch("/api/ingest", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ collection: currentCollection, records }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "ingest failed");
    const ioMs = performance.now() - t1;
    recordPerf("seekdb 写入+索引刷新", ioMs, `${records.length} 条, count=${data.count}`);
    setStatus(els.importStatus,
      `导入完成：${records.length} 个文档 → 集合 ${currentCollection}（count=${data.count}）\n` +
      `本地向量化 ${fmt(embedMs)}（吞吐 ${throughput.toFixed(1)} docs/s）· 写入+刷新 ${fmt(data.writeMs + data.refreshMs)}（其中刷新确认 ${fmt(data.refreshMs)}）`,
      "ok");
    els.importProgress.style.width = "0%";
  } catch (err) {
    setStatus(els.importStatus, "导入失败：" + err.message, "error");
  } finally {
    els.importBtn.disabled = false;
  }
}

async function importSample() {
  const res = await fetch("./sample/corpus.json");
  const docs = await res.json();
  els.importText.value = docs.map((d) => `${d.title}\n${d.text}`).join("\n\n");
  setStatus(els.importStatus, `已载入示例语料 ${docs.length} 篇，点击「开始导入」。`);
}

// ---------- 混合检索 ----------

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function highlight(text, query) {
  const terms = query.split(/\s+/).filter((t) => t.length > 1).map(escapeHtml).map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  let html = escapeHtml(text);
  for (const t of terms) {
    html = html.replace(new RegExp(t, "gi"), (m) => `<mark>${m}</mark>`);
  }
  return html;
}

async function search() {
  const q = els.query.value.trim();
  if (!q) return;
  if (!extractor) return setStatus(els.searchTiming, "请先加载模型", "error");
  if (!currentCollection) return setStatus(els.searchTiming, "请先选择集合", "error");
  els.search.disabled = true;
  try {
    const topK = Number(els.topk.value) || 10;
    const t0 = performance.now(); // 端到端起点：用户提交查询
    setStatus(els.searchTiming, "本地生成查询向量…");
    const { vectors, totalMs: queryEmbedMs } = await embedTexts([q]);
    const t1 = performance.now();
    const res = await fetch("/api/search", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ collection: currentCollection, query: q, embedding: vectors[0], topK }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "search failed");
    const e2eMs = performance.now() - t0;
    recordPerf("查询向量化(本地)", queryEmbedMs, "1 query");
    recordPerf("seekdb 混合检索", data.searchMs, `top${topK}, count=${data.count}`);
    recordPerf("端到端查询延迟", e2eMs, `top${topK}`);
    setStatus(els.searchTiming,
      `查询向量化 ${fmt(queryEmbedMs)} · seekdb 混合检索 ${fmt(data.searchMs)} · 端到端 ${fmt(e2eMs)}（返回 ${data.rows.length} 条，集合 count=${data.count}）`, "ok");
    els.results.innerHTML = data.rows.length
      ? data.rows.map((r, i) => `
        <div class="result">
          <div class="meta">#${i + 1} · <span class="score">distance=${r.distance == null ? "-" : Number(r.distance).toFixed(4)}</span> · id=${escapeHtml(r.id)} · ${escapeHtml(JSON.stringify(r.metadata || {}))}</div>
          <div class="doc">${highlight((r.document || "").slice(0, 500), q)}</div>
        </div>`).join("")
      : "<p class='hint'>无结果。尝试导入语料或调整查询词。</p>";
  } catch (err) {
    setStatus(els.searchTiming, "检索失败：" + err.message, "error");
  } finally {
    els.search.disabled = false;
  }
}

// ---------- 初始化 ----------

async function init() {
  renderPerf();
  try {
    const res = await fetch("/api/state");
    const data = await res.json();
    setBadge(els.seekdbBadge, `seekdb：${data.seekdb.mode} / ${data.seekdb.database}`, "ok");
  } catch {
    setBadge(els.seekdbBadge, "seekdb：连接失败", "warn");
  }
  const hasWebGPU = await detectWebGPU();
  if (!hasWebGPU) setBadge(els.backendBadge, "后端：未加载（无 WebGPU，将用 WASM）", "warn");
  await refreshCollections();
}

els.load.addEventListener("click", loadModel);
els.createCollection.addEventListener("click", () => createCollection().catch((e) => setStatus(els.collectionStatus, String(e), "error")));
els.deleteCollection.addEventListener("click", () => deleteCurrentCollection().catch((e) => setStatus(els.collectionStatus, String(e), "error")));
els.refreshCollections.addEventListener("click", () => refreshCollections());
els.collectionSelect.addEventListener("change", () => {
  currentCollection = els.collectionSelect.value;
  localStorage.setItem("kb.currentCollection", currentCollection);
  updateCollectionStatus();
});
els.importBtn.addEventListener("click", importDocs);
els.importSample.addEventListener("click", importSample);
els.search.addEventListener("click", search);
els.query.addEventListener("keydown", (e) => { if (e.key === "Enter") search(); });
els.exportPerf.addEventListener("click", () => {
  const blob = new Blob([JSON.stringify(perfRecords, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "perf-timings.json";
  a.click();
});
els.clearPerf.addEventListener("click", () => {
  perfRecords.length = 0;
  localStorage.setItem("kb.perf", "[]");
  renderPerf();
});

init();
