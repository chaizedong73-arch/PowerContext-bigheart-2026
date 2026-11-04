// 离线评测脚本（复现工具，不参与线上推理）：
// 在相同语料与查询集上对比 fp32（量化前基线）与 q8/fp16（量化后）的检索质量。
// 指标：Recall@5 / Recall@10 / nDCG@10 / MRR。
// 运行：node eval/evaluate.mjs [fp32 q8 fp16]   （默认 fp32 与 q8）
import { pipeline } from "@huggingface/transformers";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const corpus = JSON.parse(readFileSync(path.join(__dirname, "../public/sample/corpus.json"), "utf8"));
const queries = JSON.parse(readFileSync(path.join(__dirname, "./queries.json"), "utf8")).queries;
const ids = corpus.map((_, i) => `doc-${String(i + 1).padStart(4, "0")}`);
const texts = corpus.map((d) => `${d.title}\n${d.text}`);

function cosine(a, b) {
  let dot = 0, na = 0, nb = 0;
  for (let i = 0; i < a.length; i++) { dot += a[i] * b[i]; na += a[i] * a[i]; nb += b[i] * b[i]; }
  return dot / (Math.sqrt(na) * Math.sqrt(nb) + 1e-12);
}

function rankBySimilarity(queryVec, docVecs) {
  return docVecs
    .map((v, i) => ({ id: ids[i], score: cosine(queryVec, v) }))
    .sort((a, b) => b.score - a.score);
}

function recallAtK(ranked, relevant, k) {
  const top = ranked.slice(0, k).map((r) => r.id);
  return relevant.filter((id) => top.includes(id)).length / relevant.length;
}

function ndcgAtK(ranked, relevant, k) {
  const dcg = ranked.slice(0, k).reduce((acc, r, i) => {
    const gain = relevant.includes(r.id) ? 1 : 0;
    return acc + gain / Math.log2(i + 2);
  }, 0);
  const idcg = Array.from({ length: Math.min(relevant.length, k) }, (_, i) => 1 / Math.log2(i + 2)).reduce((a, b) => a + b, 0);
  return dcg / (idcg + 1e-12);
}

function mrr(ranked, relevant) {
  for (let i = 0; i < ranked.length; i++) {
    if (relevant.includes(ranked[i].id)) return 1 / (i + 1);
  }
  return 0;
}

async function evaluatePrecision(precisions) {
  const results = {};
  for (const precision of precisions) {
    const extractor = await pipeline("feature-extraction", "Xenova/all-MiniLM-L6-v2", { dtype: precision, device: "cpu" });
    const docOut = await extractor(texts, { pooling: "mean", normalize: true });
    const docVecs = docOut.tolist();
    const rows = [];
    for (const q of queries) {
      const qOut = await extractor([q.query], { pooling: "mean", normalize: true });
      const qVec = qOut.tolist()[0];
      const ranked = rankBySimilarity(qVec, docVecs);
      rows.push({
        id: q.id,
        r5: recallAtK(ranked, q.relevant, 5),
        r10: recallAtK(ranked, q.relevant, 10),
        ndcg: ndcgAtK(ranked, q.relevant, 10),
        mrr: mrr(ranked, q.relevant),
      });
    }
    const avg = (f) => rows.reduce((a, r) => a + f(r), 0) / rows.length;
    results[precision] = {
      recall5: avg((r) => r.r5),
      recall10: avg((r) => r.r10),
      ndcg10: avg((r) => r.ndcg),
      mrr: avg((r) => r.mrr),
      rows,
    };
    console.log(`\n=== ${precision} ===`);
    for (const r of rows) {
      console.log(`${r.id}: Recall@5=${r.r5.toFixed(2)} Recall@10=${r.r10.toFixed(2)} nDCG@10=${r.ndcg.toFixed(3)} MRR=${r.mrr.toFixed(3)}`);
    }
    console.log(`均值: Recall@5=${results[precision].recall5.toFixed(3)} Recall@10=${results[precision].recall10.toFixed(3)} nDCG@10=${results[precision].ndcg10.toFixed(3)} MRR=${results[precision].mrr.toFixed(3)}`);
  }

  console.log("\n=== 量化前后对比 ===");
  const base = results[precisions[0]];
  for (const p of precisions.slice(1)) {
    const r = results[p];
    console.log(`${p}: Recall@10 ${(base.recall10 * 100).toFixed(1)}% -> ${(r.recall10 * 100).toFixed(1)}% (Δ${((r.recall10 - base.recall10) * 100).toFixed(1)}pp), nDCG@10 ${(base.ndcg10 * 100).toFixed(1)}% -> ${(r.ndcg10 * 100).toFixed(1)}% (Δ${((r.ndcg10 - base.ndcg10) * 100).toFixed(1)}pp)`);
  }
}

const precisions = process.argv.slice(2).length ? process.argv.slice(2) : ["fp32", "q8"];
await evaluatePrecision(precisions);
