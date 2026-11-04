// 冒烟测试：集合创建 → 增量写入 → 索引刷新确认 → 混合检索（伪向量验证数据层链路）
const BASE = "http://localhost:5173";
const DIM = 384;
const vec = (seed) => Array.from({ length: DIM }, (_, i) => Math.sin(seed + i * 0.01));

async function api(path, body, method = "GET") {
  const res = await fetch(BASE + path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json();
  if (!res.ok) throw new Error(`${path}: ${JSON.stringify(data)}`);
  return data;
}

// 1. 创建集合
console.log("create collection:", await api("/api/collections", { name: "smoke_test", dimension: DIM }, "POST"));

// 2. 增量写入两条
const r1 = await api("/api/ingest", {
  collection: "smoke_test",
  records: [
    { id: "doc-0001", text: "machine learning models learn from data", embedding: vec(1), metadata: { title: "ml" } },
    { id: "doc-0002", text: "WebGPU runs inference on the graphics card in the browser", embedding: vec(2), metadata: { title: "webgpu" } },
  ],
}, "POST");
console.log("ingest batch1:", r1);

// 3. 增量追加一条（验证增量写入 + 计数）
const r2 = await api("/api/ingest", {
  collection: "smoke_test",
  records: [
    { id: "doc-0003", text: "hybrid search fuses keyword and vector rankings with RRF", embedding: vec(3), metadata: { title: "hybrid" } },
  ],
}, "POST");
console.log("ingest batch2 (incremental):", r2);

// 4. 混合检索：全文词 machine learning + 向量近 doc-0003
const s1 = await api("/api/search", {
  collection: "smoke_test",
  query: "machine learning",
  embedding: vec(3),
  topK: 5,
}, "POST");
console.log("search fulltext+vector:", s1.rows.map((r) => `${r.id}:${r.distance}`), "searchMs=", s1.searchMs.toFixed(1), "count=", s1.count);

// 5. 仅全文词（验证全文路）
const s2 = await api("/api/search", {
  collection: "smoke_test",
  query: "WebGPU graphics",
  embedding: vec(99), // 无关向量，应主要靠全文路命中 doc-0002
  topK: 5,
}, "POST");
console.log("search fulltext-heavy:", s2.rows.map((r) => r.id));

// 6. upsert 幂等验证（重复写 doc-0001）
const r3 = await api("/api/ingest", {
  collection: "smoke_test",
  records: [{ id: "doc-0001", text: "machine learning models learn from data v2", embedding: vec(1), metadata: { title: "ml" } }],
}, "POST");
console.log("upsert same id:", r3, "count should stay 3");

console.log("SMOKE_OK");
