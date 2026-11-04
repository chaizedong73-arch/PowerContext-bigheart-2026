// 离线评测计算：从 results-*.json 与 queries.json 计算 Recall@K / MRR@10 / nDCG@10 与中位延迟。
// 用法: node eval/compute-metrics.mjs <results1.json> [results2.json ...]
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const queries = JSON.parse(fs.readFileSync(path.join(__dirname, "queries.json"), "utf8")).queries;
const relMap = new Map(queries.map((q) => [q.id, q.relevant]));

const median = (xs) => {
  if (!xs.length) return null;
  const s = [...xs].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
};

// 从 "查询向量化 X ms · seekdb 混合检索 Y ms · 端到端 Z ms（...）" 提取毫秒数
function parseTiming(t) {
  const m = [...t.matchAll(/([\d.]+) ms/g)].map((x) => parseFloat(x[1]));
  return { embed: m[0] ?? null, seekdb: m[1] ?? null, e2e: m[2] ?? null };
}

function metrics(result) {
  let r5 = 0, r10 = 0, mrr = 0, ndcg = 0, n = 0;
  const embeds = [], seekdbs = [], e2es = [];
  for (const q of result.queries) {
    const rel = new Set(relMap.get(q.id) || []);
    const rank = q.rank;
    const hits5 = rank.slice(0, 5).filter((d) => rel.has(d)).length;
    const hits10 = rank.slice(0, 10).filter((d) => rel.has(d)).length;
    r5 += hits5 / rel.size;
    r10 += hits10 / rel.size;
    const first = rank.findIndex((d) => rel.has(d));
    mrr += first >= 0 ? 1 / (first + 1) : 0;
    // nDCG@10（二元相关）
    let dcg = 0;
    rank.slice(0, 10).forEach((d, i) => { if (rel.has(d)) dcg += 1 / Math.log2(i + 2); });
    const idcg = [...rel].slice(0, 10).reduce((acc, _, i) => acc + 1 / Math.log2(i + 2), 0);
    ndcg += idcg > 0 ? dcg / idcg : 0;
    n++;
    const t = parseTiming(q.timing);
    if (q.id !== "q1") { // q1 为各配置首个查询（冷启动/编译波动大），计时统计时剔除
      if (t.embed != null) embeds.push(t.embed);
      if (t.seekdb != null) seekdbs.push(t.seekdb);
      if (t.e2e != null) e2es.push(t.e2e);
    }
  }
  return {
    cfg: result.cfg,
    recall5: (r5 / n * 100).toFixed(1) + "%",
    recall10: (r10 / n * 100).toFixed(1) + "%",
    mrr10: mrr.toFixed ? mrr / n : mrr / n,
    ndcg10: (ndcg / n).toFixed(4),
    medEmbedMs: median(embeds) && median(embeds).toFixed(1),
    medSeekdbMs: median(seekdbs) && median(seekdbs).toFixed(1),
    medE2eMs: median(e2es) && median(e2es).toFixed(1),
    n,
  };
}

const files = process.argv.slice(2);
console.log("=== 检索质量与时延（14 条查询，q1 计时不入中位数） ===");
for (const f of files) {
  const data = JSON.parse(fs.readFileSync(f, "utf8"));
  if (data.error) { console.log(f, "ERROR:", data.error); continue; }
  const m = metrics(data);
  console.log(JSON.stringify({
    配置: m.cfg,
    "Recall@5": m.recall5,
    "Recall@10": m.recall10,
    "MRR@10": Number(m.mrr10).toFixed(4),
    "nDCG@10": m.ndcg10,
    "查询向量化中位ms": m.medEmbedMs,
    "seekdb检索中位ms": m.medSeekdbMs,
    "端到端中位ms": m.medE2eMs,
  }));
}
