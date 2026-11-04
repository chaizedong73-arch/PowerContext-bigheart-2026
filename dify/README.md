# 《午夜档案馆》· 规则怪谈主持人（Dify × seekdb × PowerContext）

> 一句话：一座深夜只接待一个人的档案馆。你报工号、点口味，调度台派给你一份「规则怪谈」委托；
> 主持人**只能引用检索得到的条文**给你判罚，封存规则要你自己想办法解锁；
> 而你在这个馆里做过什么，换一个会话回来，它还记得。

## 玩法

1. **报工号**（`player_id`，形如 `A-1101`）。工号即记忆归属，跨会话靠它认人。
2. **派单**：说「派单：我怕黑，喜欢跟声音有关的线索，难度中等」。口味决定选哪个场景，难度决定候选范围。
3. **行动**：用自然语言描述你做了什么（「电台报完歌名，那三秒空白里我哼了一个音」）。
4. **裁决**：主持人引用条文编号并立即兑现该条文写明的后果。它不能编造条文，也不能因为剧情好看而放水。
5. **解锁封存**：档案里有「封存中」的规则，条文内容不可检索。只有玩家真的用上写过「可解锁」的道具/NPC 线索，才会被解开并出现在可引用列表里。
6. **收集结局**：每个场景有若干结局，触发条件写在条文里，凑不齐就不给。
7. **翻旧账**：新开一个会话、用同一个工号回来，主持人会从记忆里提起你上一局的旧事（提起哪条取决于召回，见 `VALIDATION.md` S4）。

回复末尾固定带一条状态条（工号 / 场景 / 轮次 / 心情 / 本轮引用 / 累计违规 / 结局 / 已解锁封存 / 本轮写入了哪条记忆），玩家随时能核对主持人有没有说谎。

## 三者的分工（谁干了什么真实的活）

| 组件 | 承担的职责 | 少了它会怎样 |
|---|---|---|
| **seekdb**（嵌入式 pyseekdb，HNSW cosine 384 维 + `ik` 全文 + metadata where，RRF 融合） | **裁决依据的唯一来源**。每轮把玩家行动做一次三路混合检索，命中的条目才是主持人可以引用的条文；条文原文**只存在于 seekdb 库里，DSL 与提示词里一个字都没有**。「难度上限 / 场景 / 种类」是结构化 where 条件，直接决定这一局玩什么；`sealed=true` 的条目在 API 层被过滤掉，主持人只拿到编号与危险度 | 若只靠提示词写「别引用封存规则」，模型随时能凭常识编出一条规则来判罚，「未知规则」的玩法立刻塌掉。现在不可知性由检索层保证 |
| **PowerContext**（1.2.0 HTTP API） | **跨会话的玩家档案**。按工号建 scope（`idempotency_key=archive-player-<工号>`，同一个人无论怎么拼写都复用同一个 scope），每轮 `/v1/context/prepare` 读回不可信历史上下文用于翻旧账，每轮 `/v1/memory/remember` 按 `kind=progress/violation/unlock/ending` 写回本轮事实 | 会话变量只活在一局里。没有它，「上一个工号也是这么按的」这种话只能靠模型编 |
| **Dify**（advanced-chat Chatflow，18 节点） | 编排与安全边界：意图分类 → 建档 → 读记忆 → 分支取档案（派单取整套场景包 / 行动做检索）→ **反幻觉闸门**（校验主持人引用的编号必须来自本轮命中）→ 写回会话状态 → 写记忆 | 见 `RUNBOOK.md` 的流程图与逐节点说明 |

一句话概括分工：**seekdb 决定「本局有什么规矩」，PowerContext 决定「你是谁、你干过什么」，Dify 把它们缝成一个会撒谎就会被抓的主持人。**

## 目录

```
dify/
├── README.md                        本文件：创意、玩法、技术分工
├── RUNBOOK.md                       环境/版本、安装→导入→初始化→启动、重新绑定、网络条件、局限
├── VALIDATION.md                    验证样例 S1–S8：初始状态、输入、预期、实际结果、证据索引
├── dsl/midnight_archive_chatflow.yml  Dify Chatflow DSL（导入即用，配置全为占位值）
├── archive/                         seekdb 检索后端（FastAPI + 嵌入式 pyseekdb + 语料 + 灌库脚本）
│   ├── main.py                      /archive/health search scene_pack reveal scenes evidence seed（除 health 外全部要令牌）
│   ├── seekdb_store.py              Schema/索引建立、三路混合检索、RRF 融合、结构化 where（kind/scene/difficulty/danger/sealed）
│   ├── embedder.py                  MiniLM 384 维向量化（离线，带确定性哈希兜底）
│   ├── seed.py + data/scenes.json   5 个场景 / 90 条档案条目
│   ├── run.sh                       启动脚本（自动建库、自动灌库、读 .env）
│   └── .env.example                 占位配置（令牌、库路径、端口）
├── powercontext/pc_memory.py        工号规范化、scope 建档/复用、历史写入与读回（与 DSL 逐字段对齐）
├── tools/validate_dsl.py            DSL 离线校验器：结构/引用/编译 + 真实响应回放 + 与云端回导快照链路核对
├── tools/capture_fixtures.py        从本地后端抓取真实响应作为校验 fixture
├── tools/run_pc_demo.sh             PowerContext 建档/幂等/读回演示
├── capture/                         真实响应快照（校验器的输入 fixture）
└── evidence/                        运行证据：云端导出、App API 实测输出、后端探针、记忆读回、校验器日志
```

## 复用来源与改动

- 语料（5 个规则怪谈场景、90 条条目）为本项目原创撰写，未复用公开数据集。
- 三路混合检索 + RRF 融合的调用范式沿用本仓库 `2026-competition-ecosystem` 分支上已验证的 seekdb 用法，改动是：把 `kind / scene_id / difficulty_max / danger_min / sealed` 都做成可用的结构化 where 条件、把全文通道从 `search` 换成 `where_document.$contains`（`ik` 分词在本实例上对 `search` 无命中）、并在返回体里保留每条命中的通道来源以便核对。
- 封存门放在 API 层（`archive/main.py:_unlock_filter`）而不是 where 里，因为「sealed=false **或** code∈本工号已解锁列表」这个条件写不成单个 where；诊断字段仍保留 seekdb 原始三路命中，所以能区分「没搜到」和「搜到但被封锁」。
- Dify DSL 的节点写法（`raw-text` 请求体 + 代码节点 `json.dumps`、`assigner(v2)` 覆盖写会话变量、`variable-aggregator` 归并分支响应）参考了 Dify 官方导出样例，改动是把**所有**含玩家原话的请求体集中到代码节点序列化，避免原话里的引号/换行拼坏 JSON。
- 桌面端没有可复用的公开工作流；`tools/validate_dsl.py` 为本次新写的校验器。

## 已知未完成项（提交前请读）

- **仓库里的 DSL 比线上已发布版本多 3 处收紧**：铁律 3 禁止区间表述、铁律 6 记忆非空必须翻旧账、封存条目不再下发标题。这 3 处不影响 seekdb / PowerContext 链路（校验器核对过：同 18 条连线、同 27 个变量引用、同节点标题），但要完全对齐需要**重新导入 DSL 并再发布一次**。
- **跨会话「必定翻旧账」做不到形式保证**：本实例 PowerContext 没有 embedding provider，`/v1/context/prepare` 走 fts，召回集合随措辞变化（`VALIDATION.md` S4 三次取样给出两种结果）。缺条时主持人照实说「没留下违规」，不编造。
- 两个后端必须同时在线；`cloudflared` quick tunnel 的域名每次重开都会变。
- 玩家真正解锁封存条目那一局（`/archive/reveal` 正向路径）没有留线上取样，只验了过滤与解锁通道本身。
