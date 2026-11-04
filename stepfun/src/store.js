// seekdb 接入层：连接管理、集合管理、增量写入、混合检索。
// 全部存储与检索均由 seekdb 完成，客户端不做内存检索或两路结果拼合。
import { SeekdbClient, SeekdbAdminClient } from "seekdb";

const DEFAULT_COLLECTION_DIMENSION = 384; // all-MiniLM-L6-v2

function readSeekdbConfig(env = process.env) {
  const mode = (env.SEEKDB_MODE || "embedded").toLowerCase();
  if (mode === "server") {
    return {
      mode,
      host: env.SEEKDB_HOST || "127.0.0.1",
      port: Number(env.SEEKDB_PORT || 2881),
      database: env.SEEKDB_DATABASE || "knowledge_base",
      user: env.SEEKDB_USER || "root",
      password: env.SEEKDB_PASSWORD || "",
      tenant: env.SEEKDB_TENANT || undefined,
    };
  }
  return {
    mode: "embedded",
    path: env.SEEKDB_PATH || "./data/seekdb.db",
    database: env.SEEKDB_DATABASE || "knowledge_base",
  };
}

export class KnowledgeStore {
  constructor(config = readSeekdbConfig()) {
    this.config = config;
    this.client = null;
  }

  async connect() {
    if (!this.client) {
      // 确保数据库存在（嵌入式/本地 server 首次使用时会报 database is null）
      try {
        const admin = new SeekdbAdminClient(this.config);
        try {
          const db = await admin.getDatabase(this.config.database);
          if (!db) {
            await admin.createDatabase(this.config.database);
            console.log(`[seekdb] database created: ${this.config.database}`);
          }
        } catch {
          await admin.createDatabase(this.config.database).catch(() => {});
        }
        await admin.close();
      } catch (err) {
        console.warn(`[seekdb] ensure database skipped: ${err.message}`);
      }
      this.client = new SeekdbClient(this.config);
    }
    return this.client;
  }

  async close() {
    if (this.client) {
      await this.client.close();
      this.client = null;
    }
  }

  // 注：seekdb 1.4.0 的 client.listCollections() 对 v2 集合存在内部异常被吞掉、恒返回 [] 的问题，
  // 这里直接查询 seekdb 元数据表 sdk_collections（数据仍在 seekdb 内，不涉及客户端拼合）。
  async listCollections() {
    const client = await this.connect();
    let rows;
    try {
      rows = await client.execute("SELECT collection_name, settings FROM sdk_collections ORDER BY created_at DESC");
    } catch (err) {
      // 元数据表尚不存在 = 还没有任何集合
      if (String(err.message).includes("not exist") || String(err.message).includes("not found")) return [];
      throw err;
    }
    return rows.map((r) => {
      let dimension = null;
      try {
        dimension = JSON.parse(r.settings)?.schema?.vectorIndex?.hnsw?.dimension ?? null;
      } catch { /* settings 解析失败时 dimension 置空 */ }
      return { name: r.collection_name, dimension };
    });
  }

  async getOrCreateCollection(name, dimension = DEFAULT_COLLECTION_DIMENSION) {
    const client = await this.connect();
    // embeddingFunction: null —— 向量全部由浏览器端侧计算后传入，禁用服务端 embedding
    const collection = await client.getOrCreateCollection({
      name,
      embeddingFunction: null,
      configuration: { dimension, distance: "cosine" },
    });
    return collection;
  }

  async hasCollection(name) {
    const client = await this.connect();
    return client.hasCollection(name);
  }

  async deleteCollection(name) {
    const client = await this.connect();
    await client.deleteCollection(name);
  }

  // 增量写入：upsert 保证重复导入幂等；写入后轮询确认新 id 可见（索引刷新）。
  // 返回写入耗时与刷新确认耗时，供性能报告使用。
  async ingest(collectionName, records, dimension = DEFAULT_COLLECTION_DIMENSION) {
    const collection = await this.getOrCreateCollection(collectionName, dimension);
    const writeStart = performance.now();
    const ids = records.map((r) => r.id);
    await collection.upsert({
      ids,
      documents: records.map((r) => r.text),
      embeddings: records.map((r) => r.embedding),
      metadatas: records.map((r) => r.metadata || {}),
    });
    const writeMs = performance.now() - writeStart;

    // 索引刷新：V1.3.0+ HNSW 异步维护，写入后显式刷新 + 轮询确认新 id 可检索。
    const refreshStart = performance.now();
    await collection.refresh_index();
    const pending = new Set(ids);
    const deadline = refreshStart + 15000;
    let refreshMs = 0;
    while (pending.size > 0 && performance.now() < deadline) {
      const got = await collection.get({ ids: [...pending] });
      for (const id of got.ids || []) {
        pending.delete(id);
      }
      if (pending.size === 0) break;
      await new Promise((r) => setTimeout(r, 100));
    }
    refreshMs = performance.now() - refreshStart;
    if (pending.size > 0) {
      throw new Error(`index refresh timeout, ${pending.size} ids not visible after 15s`);
    }
    const count = await collection.count();
    return { writeMs, refreshMs, count };
  }

  // 混合检索：全文 + 向量两路均在 seekdb 内执行并由 RRF 融合，
  // 不在客户端拼合两路结果。
  async hybridSearch(collectionName, { queryText, embedding, topK = 10, where }) {
    const collection = await this.getOrCreateCollection(collectionName);
    const searchStart = performance.now();
    const result = await collection.hybridSearch({
      query: {
        whereDocument: { $contains: queryText },
        nResults: Math.max(topK, 10),
      },
      knn: {
        queryEmbeddings: [embedding],
        nResults: Math.max(topK, 10),
        ...(where ? { where } : {}),
      },
      rank: { rrf: {} },
      nResults: topK,
      include: ["documents", "metadatas", "distances"],
    });
    const searchMs = performance.now() - searchStart;
    const count = await collection.count();
    const ids = result.ids?.[0] ?? result.ids ?? [];
    const documents = result.documents?.[0] ?? result.documents ?? [];
    const metadatas = result.metadatas?.[0] ?? result.metadatas ?? [];
    const distances = result.distances?.[0] ?? result.distances ?? [];
    const rows = ids.map((id, i) => ({
      id,
      document: documents[i] ?? null,
      metadata: metadatas[i] ?? null,
      distance: distances[i] ?? null,
    }));
    return { rows, searchMs, count };
  }

  async listDocuments(collectionName, limit = 100, offset = 0) {
    const collection = await this.getOrCreateCollection(collectionName);
    const got = await collection.get({
      limit,
      offset,
      include: ["documents", "metadatas"],
    });
    const ids = got.ids ?? [];
    const documents = got.documents ?? [];
    const metadatas = got.metadatas ?? [];
    return ids.map((id, i) => ({ id, document: documents[i], metadata: metadatas[i] }));
  }

  async count(collectionName) {
    const collection = await this.getOrCreateCollection(collectionName);
    return collection.count();
  }

  async deleteDocuments(collectionName, ids) {
    const collection = await this.getOrCreateCollection(collectionName);
    await collection.delete({ ids });
    return collection.count();
  }
}
