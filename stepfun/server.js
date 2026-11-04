// Express 服务：托管前端静态资源，提供 seekdb 数据层 REST API。
// 推理不在这里发生——embedding 全部由浏览器本地计算后传入。
import express from "express";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { KnowledgeStore } from "./src/store.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PORT = Number(process.env.PORT || 5173);

const store = new KnowledgeStore();
await store.connect();
console.log(`[seekdb] connected: mode=${store.config.mode} database=${store.config.database}`);

const app = express();
app.use(express.json({ limit: "64mb" }));
app.use(express.static(path.join(__dirname, "public")));

// 当前连接信息（模式、数据库）
app.get("/api/state", (req, res) => {
  res.json({
    seekdb: { mode: store.config.mode, database: store.config.database },
    model: { name: "Xenova/all-MiniLM-L6-v2", dimension: 384 },
  });
});

app.get("/api/collections", async (req, res) => {
  try {
    res.json({ collections: await store.listCollections() });
  } catch (err) {
    res.status(500).json({ error: String(err.message || err) });
  }
});

app.post("/api/collections", async (req, res) => {
  try {
    const { name, dimension } = req.body;
    if (!name) return res.status(400).json({ error: "name is required" });
    if (!/^[a-zA-Z0-9_]+$/.test(name)) {
      return res.status(400).json({ error: "collection name only allows letters, digits, underscore: [a-zA-Z0-9_]" });
    }
    await store.getOrCreateCollection(name, dimension || 384);
    res.json({ ok: true, name });
  } catch (err) {
    res.status(500).json({ error: String(err.message || err) });
  }
});

app.delete("/api/collections/:name", async (req, res) => {
  try {
    await store.deleteCollection(req.params.name);
    res.json({ ok: true });
  } catch (err) {
    res.status(500).json({ error: String(err.message || err) });
  }
});

// 增量导入：records = [{id, text, metadata}], embeddings 由浏览器本地计算
app.post("/api/ingest", async (req, res) => {
  try {
    const { collection, records } = req.body;
    if (!collection || !Array.isArray(records) || records.length === 0) {
      return res.status(400).json({ error: "collection and records are required" });
    }
    for (const r of records) {
      if (!r.id || typeof r.text !== "string" || !Array.isArray(r.embedding)) {
        return res.status(400).json({ error: "each record needs id, text, embedding" });
      }
    }
    const result = await store.ingest(collection, records);
    res.json({ ok: true, ...result });
  } catch (err) {
    res.status(500).json({ error: String(err.message || err) });
  }
});

// 混合检索：queryText 供全文路，embedding 供向量路，seekdb 内部 RRF 融合
app.post("/api/search", async (req, res) => {
  try {
    const { collection, query, embedding, topK } = req.body;
    if (!collection || typeof query !== "string" || !Array.isArray(embedding)) {
      return res.status(400).json({ error: "collection, query, embedding are required" });
    }
    const result = await store.hybridSearch(collection, { queryText: query, embedding, topK: topK || 10 });
    res.json({ ok: true, ...result });
  } catch (err) {
    res.status(500).json({ error: String(err.message || err) });
  }
});

app.get("/api/documents", async (req, res) => {
  try {
    const { collection, limit, offset } = req.query;
    if (!collection) return res.status(400).json({ error: "collection is required" });
    const docs = await store.listDocuments(collection, Number(limit) || 100, Number(offset) || 0);
    res.json({ documents: docs });
  } catch (err) {
    res.status(500).json({ error: String(err.message || err) });
  }
});

app.post("/api/documents/delete", async (req, res) => {
  try {
    const { collection, ids } = req.body;
    if (!collection || !Array.isArray(ids)) return res.status(400).json({ error: "collection and ids are required" });
    const count = await store.deleteDocuments(collection, ids);
    res.json({ ok: true, count });
  } catch (err) {
    res.status(500).json({ error: String(err.message || err) });
  }
});

app.listen(PORT, () => {
  console.log(`[app] listening on http://localhost:${PORT}`);
});
