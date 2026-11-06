"""《午夜档案馆》配套检索后端：把 seekdb 混合检索能力暴露成 Dify HTTP 节点可调用的小服务。

设计要点
--------
* 服务本身**不做任何 LLM 判断**，只负责「档案的存储、查询与检索」；编排、裁决、生成全部
  留在 Dify Chatflow 里，因此 seekdb 在应用中的职责边界清晰可核对。
* 每个响应都带 `retrieval` 字段，说明本次命中走了哪条 seekdb 通道（向量 / 全文 / 结构化 /
  RRF 融合）以及各自的命中编号，直接用于赛事要求的「技术实际发挥作用」证据。
* 封存规则（metadata.sealed=true）在未解锁时**条文与标题都不出网关**：`search` 只以 `sealed_gated`
  交回「哪些编号还封着 + 危险度」，`scene_pack` 同理放在 `sealed_locked`；原文只有 `reveal` 被调用、
  且编号进入下一轮的已解锁列表之后，才会正常出现在检索结果里。
* `search` 另附 `scene_endings`（本场景全部结局条文，结局本来就不封存）：行动轮的 top_k 撞不撞得中
  结局条文全看措辞，主持人缺了触发条件全文就永远无法判定「达成结局」，只能一直推进。
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

import fastapi
from fastapi import Header, HTTPException
from pydantic import BaseModel, Field

import seekdb_store as store


class SearchBody(BaseModel):
    query: str = Field(..., max_length=2000)
    scene_id: Optional[str] = None
    kinds: Optional[List[str]] = None
    kinds_csv: Optional[str] = None
    keywords: Optional[List[str]] = None
    keywords_csv: Optional[str] = None
    danger_min: Optional[int] = None
    top_k: int = 8
    revealed_codes: Optional[List[str]] = None
    revealed_csv: Optional[str] = None

    def resolved_kinds(self) -> Optional[List[str]]:
        return self.kinds or _csv(self.kinds_csv)

    def resolved_keywords(self) -> Optional[List[str]]:
        return self.keywords or _csv(self.keywords_csv)

    def resolved_revealed(self) -> Optional[List[str]]:
        return self.revealed_codes or _csv(self.revealed_csv)


class ScenePackBody(BaseModel):
    taste: str = Field(default="", max_length=2000)
    scene_id: Optional[str] = None
    difficulty: Optional[int] = None
    revealed_codes: Optional[List[str]] = None
    revealed_csv: Optional[str] = None
    # 玩家明确要求换一幕时，派单要把上一幕排除掉。只说「换个场景」是没有口味词的，
    # 纯按向量最近的那一幕常常还是刚才那一幕——排除了才真的换。
    exclude_scene_id: Optional[str] = None

    def resolved_revealed(self) -> Optional[List[str]]:
        return self.revealed_codes or _csv(self.revealed_csv)


class RevealBody(BaseModel):
    scene_id: str
    code: str
    reason: str = ""


class SeedBody(BaseModel):
    confirm: str


def build_app():
    global app
    app = fastapi.FastAPI(title="Midnight Archive · seekdb 检索后端", version="1.0.0")
    token = os.environ.get("ARCHIVE_TOKEN", "")

    @app.on_event("startup")
    def _warmup() -> None:
        """启动即打开 seekdb 实例并加载 embedding，避免 Dify 的第一次调用撞上冷启动。"""
        try:
            store.stats()
        except Exception:  # 尚未灌库时留给 /archive/health 暴露原因
            pass

    def guard(presented: Optional[str]) -> None:
        if token and presented != token:
            raise HTTPException(status_code=401, detail="invalid archive token")

    @app.get("/archive/health")
    def health() -> Dict[str, Any]:
        try:
            return {"ok": True, "stats": store.stats()}
        except Exception as exc:  # 冷启动时集合可能尚未建立
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:200]}

    @app.post("/archive/search")
    def search(body: SearchBody, x_archive_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
        guard(x_archive_token)
        res = store.hybrid_search(
            body.query,
            keywords=body.resolved_keywords(),
            kinds=body.resolved_kinds(),
            scene_id=body.scene_id or None,
            danger_min=body.danger_min,
            n_results=max(1, min(int(body.top_k or 8), 30)),
        )
        revealed = set(body.resolved_revealed() or [])
        gated = [
            {"code": r["code"], "danger": r.get("danger"), "state": "sealed", "from": "hit"}
            for r in res["hits"]
            if r.get("sealed") and r["code"] not in revealed
        ]
        if body.scene_id:
            # top_k 只有几条，行动文本未必能撞上封存规则本身；把本场景仍封存的编号也交出去，
            # 玩家做对了动作才有的解。这两个来源都只有 code+danger，条文与标题照旧不下发。
            seen = {g["code"] for g in gated}
            for r in store.list_by_filter(kinds=["rule"], scene_id=body.scene_id, limit=40):
                if r["sealed"] and r["code"] not in revealed and r["code"] not in seen:
                    gated.append({"code": r["code"], "danger": r.get("danger"), "state": "sealed", "from": "scene_rules"})
                    seen.add(r["code"])
            # 结局条文是反过来的问题：它不封存，却全看措辞才挤得进 top_k。玩家这一轮正好凑齐
            # 触发条件、主持人手里却没有条件全文时，「条件没凑齐就不给结局」这条铁律会让本局
            # 永远封不了盘。所以按 scene_id 把本场景结局整条交回——派单轮本来就是整套下发，
            # 这不是新的暴露面，透露与否仍由提示词管着。
            res["scene_endings"] = [
                {"code": r["code"], "title": r.get("title"), "danger": r.get("danger"), "document": r.get("document")}
                for r in store.list_by_filter(kinds=["ending"], scene_id=body.scene_id, limit=10)
            ]
            res["scene_endings_note"] = (
                "本场景全部结局条文（本来就不封存）。行动轮检索未必命中，故整条交回，主持人据此判定触发条件是否凑齐；"
                "没凑齐不许提前向玩家透露条件"
            )
        res["hits"] = _unlock_filter(res["hits"], body.resolved_revealed())
        res["sealed_gated"] = gated
        res["sealed_gated_note"] = (
            "来源 hit=seekdb 三路命中后被 API 层 _unlock_filter 挡下；来源 scene_rules=本场景 where 查询里仍封存的规则。"
            "两者只给 code/danger，条文与标题都不下发——主持人据此判断玩家动作是否满足道具写明的用途，满足才允许解锁"
        )
        return res

    @app.post("/archive/scene_pack")
    def scene_pack(body: ScenePackBody, x_archive_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
        """开局：按玩家口味（向量）+ 难度（结构化）选场景，并取回该场景档案包。"""
        guard(x_archive_token)
        scene_id = body.scene_id
        pick: Dict[str, Any] = {}
        if not scene_id:
            pick = store.hybrid_search(
                body.taste or "一间安静的房",
                kinds=["scene"],
                difficulty_max=body.difficulty,
                n_results=5,
            )
            if not pick["hits"]:
                return {"ok": False, "reason": "no_scene_match", "retrieval": pick["channels"], "taste": body.taste}
            chosen = pick["hits"]
            drop = str(body.exclude_scene_id or "").strip()
            if drop:
                fresh = [h for h in chosen if h.get("scene_id") != drop]
                # 只有 5 个场景，全都排除完了就退回第一名，不让玩家卡在「换不出来」
                if fresh:
                    chosen = fresh
                    pick["excluded_scene_id"] = drop
            scene_id = chosen[0]["scene_id"]

        scene = store.scene_brief(scene_id)
        if not scene:
            raise HTTPException(status_code=404, detail=f"unknown scene_id {scene_id}")

        revealed = set(body.resolved_revealed() or [])
        packs: Dict[str, List[Dict[str, Any]]] = {}
        locked: List[Dict[str, Any]] = []
        for kind in ("rule", "item", "npc", "omen", "ending"):
            rows = store.list_by_filter(kinds=[kind], scene_id=scene_id, limit=40)
            if kind == "rule":
                for r in rows:
                    if r["sealed"] and r["code"] not in revealed:
                        locked.append({"code": r["code"], "danger": r["danger"], "state": "sealed"})
                        continue
                    packs.setdefault(kind, []).append(r)
            else:
                packs[kind] = rows

        return {
            "ok": True,
            "scene": {
                "scene_id": scene["scene_id"],
                "title": scene["title"],
                "one_line": scene.get("one_line", ""),
                "brief": scene.get("brief", ""),
                "tone": scene.get("tone", ""),
                "difficulty": scene.get("difficulty"),
                "danger": scene.get("danger"),
                "tags": scene.get("tags"),
            },
            "pack": packs,
            "sealed_locked": locked,
            "retrieval": {
                "scene_choice": (pick.get("channels") if pick else None),
                "scene_query_used": (pick.get("query") if pick else body.scene_id),
                "structured_filters": {
                    "seekdb_where": {"scene_id": scene_id, "difficulty_max": body.difficulty},
                    "reroll_excluded_scene_id": pick.get("excluded_scene_id") or "",
                    "sealed_gate": "API 层 _unlock_filter：sealed 且未解锁的条目不进 pack，只把编号+危险度放进 sealed_locked（原文不下发）",
                },
                "engine": "seekdb embedded (HNSW cosine + ik fulltext + metadata where)",
            },
        }

    @app.post("/archive/reveal")
    def reveal(body: RevealBody, x_archive_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
        """解锁一条封存规则：只有在互动中拿到对应线索后才允许调用，返回权威原文。"""
        guard(x_archive_token)
        rows = store.list_by_filter(kinds=["rule"], scene_id=body.scene_id, include_sealed=True, limit=40)
        hit = next((r for r in rows if r["code"] == body.code), None)
        if not hit:
            raise HTTPException(status_code=404, detail=f"rule {body.code} not found in {body.scene_id}")
        return {"ok": True, "rule": hit, "reason": body.reason, "engine": "seekdb.get(where metadata)"}

    @app.get("/archive/scenes")
    def scenes() -> Dict[str, Any]:
        return {"ok": True, "scenes": store.corpus_scenes(), "stats": store.stats()}

    @app.get("/archive/evidence")
    def evidence(limit: int = 20, x_archive_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
        guard(x_archive_token)
        return {"ok": True, "calls": store.recent_calls(limit), "stats": store.stats()}

    @app.post("/archive/seed")
    def seed(body: SeedBody, x_archive_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
        guard(x_archive_token)
        if body.confirm != "REBUILD":
            raise HTTPException(status_code=400, detail='confirm must be "REBUILD"')
        t0 = time.time()
        out = store.seed(recreate=True)
        out["dataFile"] = str(store.DATA_FILE)
        out["tookMs"] = int((time.time() - t0) * 1000)
        return out

    return app


def _csv(value: Optional[str]) -> Optional[List[str]]:
    """Dify 代码节点输出标量最省事，因此约定用逗号分隔字符串传数组。"""
    if not value:
        return None
    items = [x.strip() for x in str(value).replace("，", ",").split(",")]
    return [x for x in items if x] or None


def _unlock_filter(rows: List[Dict[str, Any]], revealed_codes: Optional[List[str]]) -> List[Dict[str, Any]]:
    revealed = set(revealed_codes or [])
    kept = [r for r in rows if not r.get("sealed") or r.get("code") in revealed]
    for i, r in enumerate(kept, start=1):
        r["rank"] = i
    return kept


app = build_app()
