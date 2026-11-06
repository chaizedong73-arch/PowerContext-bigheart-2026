# 《午夜档案馆》验证样例与运行证据

每条样例都写清「初始状态 → 输入 → 预期 → 实际结果 → 证据位置」，并且只挑**能证明 seekdb / PowerContext 真的在干活**的行为来验，不验「界面能不能打开」。

## 0. 共用初始状态

| 项 | 状态 |
| --- | --- |
| seekdb 后端 | `archive/run.sh` 已启动，`GET /archive/health → {"ok":true,...}`；`pyseekdb` 嵌入式实例，库内 **5 个场景 / 90 条条目**（每场景 6 规则 + 3 道具 + 2 NPC + 3 预兆 + 3 结局 = 17 条，外加场景行本身）。health 自报 `engine=seekdb(embedded)`、`dimension=384`、`indexes={vector:HNSW/cosine, fulltext:ik}`、`embeddingProvider=sentence-transformers:paraphrase-multilingual-MiniLM-L12-v2`——**向量这一路是真 MiniLM，不是哈希兜底**；没配 embedding provider 的是 PowerContext（下一行） |
| 场景难度分布 | 电梯 1｜电台 2｜自习室 3｜绿皮车 4｜便利店 5（每场景恰好 2 条 `sealed=true` 规则） |
| PowerContext | `powercontext server run` @ 127.0.0.1:8000，v1.2.0；该实例未配 embedding provider，`/v1/context/prepare` 走 fts 召回 |
| Dify | 云端 advanced-chat，**线上已发布版本 = `375be8cc-0c15-4050-953d-c3d34c7e6689`（version `2026-11-06 13:45:23.779248`，20 节点 / 20 连线 / 28 个变量引用）**，与仓库提交的 DSL 同一份（S8 逐节点/逐连线/逐引用比对，`evidence/cloud_publish_parity.txt` 第 2–5 节 0 实质差异、9 份运行记录全部钉在这个版本号上）。两个 LLM 节点都绑 `deepseek-flash`（chat），主持人节点的对话记忆窗口是**关的**（跨轮事实只从会话变量与记忆摘要进来，见 RUNBOOK §9）。**9 个取证场景（`s3/s10/s11/s12/s13/s14/s15/s16/s17`，工号 `A-CLOUD-S80…S89`）整套重采在这版上**，共 24 轮，落在 `evidence/cloud_run_*.txt`：每轮带 `workflow_run_id` + 逐节点执行记录 + 泄露自检行，`workflow_id` 就是上面那个发布版本号，文件头记 `sha256(dsl)` 与 `git HEAD` 把证据钉回提交文件。上一版 `ad854abe` 的证据已被这一轮覆盖——换版的原因是它实测会整轮塌兜底，见 S10 |
| 运行证据形态 | Dify App API 的 blocking 响应里**没有** `workflow_run_id`；streaming（SSE）里才有 `workflow_started` / `node_started` / `node_finished` / `workflow_finished`。取云端节点级证据用 [tools/cloud_run_trace.py](tools/cloud_run_trace.py)（解析器已用离线样本自测通过） |
| 会话初始变量 | `scene_id=''`、`scene_title=''`、`revealed=''`、`violations=''`、`endings=''`、`turn=0`、`stage=idle`（即真正的第一轮，没有任何隐藏状态） |
| 令牌 | 后端 `.env` 的 `ARCHIVE_TOKEN` 与 Dify 环境变量 `ARCHIVE_TOKEN` 一致（占位值 `change-me-local-dev`） |

复现命令模板（**key / 域名一律占位**）：

```bash
curl -sS -X POST "$DIFY_API_BASE/v1/chat-messages" \
  -H "Authorization: Bearer $DIFY_APP_KEY" -H "Content-Type: application/json" \
  --data-binary @body.json
# body.json: {"inputs":{"player_id":"A-V1-03"},"query":"…口味…","response_mode":"blocking",
#             "conversation_id":"","user":"validator"}
```

## S1 · 访问控制：不带令牌就调不到档案

- **初始状态**：后端在线；未解锁任何封存条目。
- **输入**：对 `POST /archive/search` 分别用「无令牌 / 错令牌 / 对令牌」各请求一次。
- **预期**：前两种 401 并给出明确 `detail`；只有对令牌返回 200 命中结果——证明组件不是「装了但没接」，请求真的穿过鉴权进到检索层。
- **实际结果**：`no-token -> 401`、`bad-token -> 401`、`ok-token -> 200`（hits 4 条）。
- **证据**：[evidence/archive_probe.txt](evidence/archive_probe.txt) §1。

## S2 · V1 口味 → 场景（seekdb 向量召回决定开局）

- **初始状态**：全新工号 `A-V1-03`，PowerContext 里没有它的 scope，会话变量全空。
- **输入**：`今晚给我派个活，我怕黑，喜欢跟声音有关的线索，难度中等`
- **预期**：`intent_llm` 归一出 `intent=start / difficulty=3 / keywords=声音,线索,黑暗` → `req_code` 用**玩家原话+关键词**（不含第三人称概括）拼 `taste` → seekdb 以 `kind=scene` + `difficulty_max=3` 做混合检索 → 命中《礁石村的深夜电台》；档案块只含该场景 4 条可读规则，主持人只能引用这些编号；记忆为空时明说「档案袋是空的」。
- **实际结果**：场景 = `礁石村的深夜电台`，心情 `uneasy`，状态条 `4 条规则可读｜封存 2 条`；老板原话逐字复述了 `RULE-RAD-01`「音量在1以下不算听」与 `RULE-RAD-04`「谁敲门都光脚开」；开头是「档案袋是空的，工号 A-V1-03，第一次见你」；写入记忆 `kind=progress`，新建 scope `scp_34mjddtg8mf9jfjj777y5msqj2`；耗时 26.2 s。
- **判读**：条文原文只能来自 seekdb 取回的档案包（语料不在 DSL 里，模型没有任何本地副本）；「怕黑 + 声音」被向量化后选中电台场景，而不是随机的另一份。
- **证据**：[evidence/dify_app_api_samples.txt](evidence/dify_app_api_samples.txt) 的 V1 段（含 conv / msg id）。

## S3 · V2 违规必须兑现书面后果（同会话两轮，跨节点状态回写）

- **初始状态**：工号 `A-V2-01`，第一轮已派单到电台场景，`violations=''`。
- **输入**：`电台刚报完歌名，那三秒空白里我哼了一个音。`
- **预期**：语料里 `RULE-RAD-02《三秒空白里不许出声》` 的后果是「第二天她点你的歌，当晚不能再听，天亮前必须把收音机搬到别的房间」→ 主持人必须判违规、把后果演出来、把编号写进状态条、`host_code` 以 `kind=violation` 写回 PowerContext。
- **实际结果**：叙述出现「你哼的那个音升高了半度，从喇叭里钻出来……『……记住了。』」并明确「按 RULE-RAD-02，明天她会点你的歌；被点的人当晚不能再听电台，天亮前必须把收音机搬到别的房间」；状态条 `⛔ 累计违规：RULE-RAD-02`；本轮档案命中 5 条编号；`kind=violation`，scope `scp_6efv4s3g3daf8rm9b0mn4gw8ng`；耗时 17.8 s。
- **判读**：后果文案与语料一一对应（见 [evidence/rad_rules.txt](evidence/rad_rules.txt)），没有「为了剧情好看而放水」；违规计数进的是 Dify 会话变量，长期记录进的是 PowerContext，两条链路分别可见。
- **证据**：samples 文件 V2 段；条文原文对照在 `evidence/rad_rules.txt`。

## S4 · V3 跨会话翻旧账（PowerContext 的唯一不可替代点）

- **初始状态**：工号 `A-V2-01` 在 scope `scp_6efv4s3g3daf8rm9b0mn4gw8ng` 里已有 `progress` + `violation` 两类条目；**开一个全新 conversation**（会话变量全部回到初始状态）。
- **输入 / 预期**：玩家不重述任何历史，主持人仍应能从记忆里点出旧事；且只能照抄记忆里的内容，不许编造。
- **实际结果（三次取样，措辞不同）**：
  - V3（`派单：还是那间海边的民宿，继续`）→ 没有翻旧账，只说「海边的民宿不在这一本活页里」。这是 fts 没召回，见下面的局限。
  - **V3b（`今晚给我派个活，还是海边那间房，继续`）→ 「你上一回说怕黑、说想靠声音辨位，我记着」**，并接一句「我先念位置，不念内容；我念完了，没说我信」——正是把不可信历史数据当数据用的写法；scope 与 V2 **完全一致**（同工号复用同一档案袋）。
  - **V3c（`今晚给我派个活，把违反过规则的旧账算进来`）→ 「你问旧账——档案里你上一单没留下违规，只留了一句『怕黑、爱听声音』」**：本轮 prepare 只召回了 progress 条目，于是主持人照实报告「没留下违规」，**没有把记忆里没有的 RULE-RAD-02 编出来**。这既是「不许补写」的证据，也是 fts 措辞敏感的直接证据（同一工号换一种问法，召回集合就变了）。
- **判读**：`V3b/V3c` 里的旧事在**当前会话从没被说过**，只能来自 PowerContext；跨会话记忆链路成立。召回率不是 100%，且缺条时选择照实说而不是编造。
- **取样时间**：以上是**当时那版 DSL**（只读 `prepare` 一路）的行为。仓库现在的 DSL 把记忆改成 `prepare` + `entries/list` 两路并读去重，并由 S9 的 `当前场景=` 标签兜住续局；**这一改动还没在云端跑过**，S4 的线上结论需要用 §未通过项 1 的方式重采一次。措辞敏感本身没有消失（fts 还是 fts），消失的是「原话召不到就彻底失忆」。
- **量化根因**（不靠猜，见 [tools/pc_recall_matrix.py](tools/pc_recall_matrix.py) → [evidence/pc_recall_matrix.txt](evidence/pc_recall_matrix.txt)）：同一条写着「违反 RULE-RAD-02」的记忆，用玩家原话「继续」「我刚才是不是犯规了」「有人吗」去 `prepare` 都是 **0 命中**，查询里带上档案词汇才命中；而 `entries/list` 两种问法都恒返回全部条目。
- **证据**：samples 文件 V3 / V3b / V3c 段（含 3 个不同的 conversation_id）+ 召回矩阵文件。

## S5 · V4 难度上限改变派单（结构化过滤，不是纯语义）

- **初始状态**：两个新工号，同一口味（声音线索），只有难度表述不同；`difficulty` 走 `intent_llm → req_code → scene_pack.difficulty → seekdb where difficulty_max`。
- **输入 / 预期**：
  - `我喜欢跟声音有关的线索，难度只要最简单的` → `difficulty=1`，候选只剩 `difficulty<=1` 的场景 → **只能**是《末班电梯只停偶数层》。
  - `我喜欢跟声音有关的线索，难度中等` → `difficulty=3`，候选 = 电梯(1)/电台(2)/自习室(3) → 语义优选《礁石村的深夜电台》（该场景 tags 含「声音」）。
- **实际结果**：V4-01 → 电梯场景，且主持人如实说明「馆里今晚只有这一份——RULE-ELV-01 到 RULE-ELV-05 就是全部条文，没有更简单的一份可以换给你」；V4-02 → 电台场景。两轮耗时 14.4 / 15.7 s，各自新建 scope。
- **判读**：**同口味只差难度就换场景**，这是语义检索做不到的区分，只有 `where difficulty_max` 才能解释——直接证明 seekdb 的结构化过滤参与了这个决定。
- **顺带发现并修掉的缺陷**：V4-01 那句「RULE-ELV-01 到 RULE-ELV-05」用区间说法把封存中的 `RULE-ELV-04` 裹进去了 → 铁律 3 增加「禁止区间表述」；该修复尚未发布到云端（见 §未通过项）。
- **证据**：samples 文件 V4 段。

## S6 · 封存规则：条文不下发、解锁后才可引用

- **初始状态**：`scn_seaside_radio` 有 6 条规则，其中 `RULE-RAD-05/06` 为 `sealed=true`。
- **输入 / 预期**：`revealed_csv=""` 时 `hits` 只应含 4 条可读规则；`revealed_csv="RULE-RAD-05"` 时 `RULE-RAD-05` 才进入可引用集合；`scene_pack` 的 `sealed_locked` 只应含编号与危险度，**不能含标题或原文**（否则玩家能从标题反推）。
- **实际结果**：4 hits → 5 hits，`RULE-RAD-06` 始终被挡；`sealed_locked = [{"code":"RULE-RAD-05","danger":4,"state":"sealed"}, ...]` 无 title；状态条显示「4 条规则可读｜封存 2 条」。
- **判读**：三层保证——后端过滤、档案块渲染（封存行只有编号+危险度）、提示词禁止推测；同时 `retrieval` 诊断里能看到 seekdb 原始三路命中**包含**被封存的编号，说明「sealed=false 或 code∈已解锁」这条门是在 API 层兑现的（它写不成单个 where），而不是「根本没搜到」。
- **证据**：[evidence/archive_probe.txt](evidence/archive_probe.txt) §2–4；断言固化在 [tools/validate_dsl.py](tools/validate_dsl.py)（封存编号出现在可引用集合、或带《标题》出现，即报错）；**解锁的正向路径见 S9**。

## S7 · 工号幂等：脏写法仍复用同一个记忆档案袋

- **输入**：`" A\"22/01 "` 与 `"A22/01!"` 两次 `ensure_scope`。
- **预期**：规范化后同一个 `idempotency_key=archive-player-A2201`，两次返回**同一个** scope_id。
- **实际结果**：两次都得到 `scp_6c3gae54c879p1abbj6sj301a9`；`A-VALIDATE-1` 由 DSL 建的 scope 用同一套规范化规则读回，能列出 `violation` / `unlock` 两条条目。
- **证据**：[evidence/pc_memory_run.txt](evidence/pc_memory_run.txt)、[evidence/pc_readback.txt](evidence/pc_readback.txt)（含 prepare 的 `BEGIN/END_POWERCONTEXT_PREPARED_CONTEXT_V1` 不可信历史信封）。

## S8 · 离线校验：不需要联网也能证明流程正确

```bash
python3 dify/tools/capture_fixtures.py   # 抓后端真实响应到 dify/capture/
python3 dify/tools/validate_dsl.py       # 结构 + 引用 + 代码节点编译 + 真实响应回放
```

- **覆盖**：20 节点 / 20 连线；28 个不同变量引用全部有真实来源；所有代码节点可编译；用真实 seekdb / PowerContext 响应回放 `archive_code`、`memory_code`、`host_code`、`req_code`、`req2_code`、`intent_code`；对立输入（原话含引号、换行、花括号、残缺 JSON、502 HTML、逗号串冒充数组）都必须走兜底而不是抛异常；`reroll` 只认词面明确换单（「继续/接着讲/继续上一局」必须为 0）；fts 召回为空时 `entries/list` 这一路必须单独把条目拼进记忆块并解出场景；封存编号若进入可引用集合、或带《标题》出现、或未解锁编号留在叙述里，都报错；与云端回导快照做逐节点/逐连线/逐引用比对。
- **当前结果**：`0 errors, 0 warnings`（完整日志 [evidence/validator_stdout.txt](evidence/validator_stdout.txt)）。这一轮把三类「模型输出坏形状」钉成了断言，而不是只测正常路径：
  1. **推理正文泄露**：`<think>` 成对闭合、被 `max_tokens` 截断、思考里带花括号三种，都必须既吐不出 `think>`/`dify-deepseek-reasoning`，也吐不出「我不能…」这类内心独白；截断那一律回固定文案。
  2. **正文兜底**（云端 `s10` 第 3 轮实测到的形状）：主持人只写了主持文本、一个字节 JSON 都没吐，还把自己的 `🪪 工号…` 状态条拼全了。断言要求 `_prose` 只在思考标签**成对闭合**时取正文当叙述、截掉状态条、并且**照样过封存编号闸门**（`narration_redacted` 必须记上被抹掉的编号）；思考被截断时仍只回固定文案。
  3. **行动轮真放行**：拿后端真实响应回放 `archive_code`，要求 `locked_csv` 非空（否则 `revealed_now` 永远被筛光、解锁结构性不可能）、封存行不带《标题》、封存编号不在可引用集合里；再各跑一次解锁与结局，检查 `revealed_next` / `endings_next` / `stage_next=ended` / `mem_kind` 与记忆文案。
- **与云端的关系**：S8 证的是「提交文件里那段节点代码的行为」，S10 证的是「线上这一版真的这么跑了」——两份互相咬合，任何一份单独都不算闭环。
- **历史作用**：S5 的「口味污染」缺陷（第三人称概括混进 `taste` 导致派单跑偏）就是在这里定位的——把两种 `taste` 字符串各跑一遍检索，结果不同，直接坐实原因。

## S9 · 解锁与结局的正向路径（之前只验了「挡得住」，这条验「放得开」）

```bash
cd dify && python3 tools/reveal_sample.py     # 两个后端在本地起着才能跑
```

- **初始状态**：`scn_seaside_radio` 的 `RULE-RAD-05/06` 都 `sealed=true`；每次跑用**新工号** `A-REVEAL-<时间戳>`，不污染既有档案袋。
- **A. 解锁链条（36 条断言全过，日志 [evidence/reveal_positive_path.txt](evidence/reveal_positive_path.txt)）**：
  1. 未解锁时，指向封存内容的查询在 `hits` 里没有 `RULE-RAD-05`——但同一份响应的 `retrieval.channels` 里能看到 seekdb 三路**本来搜到了它**，证明是 API 层挡的；
  2. `scene_pack` 把它放进 `sealed_locked`（只有 `code/danger/state`，无标题无原文）；DSL「拼装档案块」渲染出的封存行同样只有编号+危险度；
  3. `/archive/reveal {scene_id, code: RULE-RAD-05}` 返回权威原文「报到你父亲那艘船时，拔插头…」，库里该条目 `sealed=True`；不存在的编号返回 **404**（不是静默放行）；
  4. **同一句话、同一个库**，只把 `revealed_csv` 改成 `RULE-RAD-05` 再搜：它出现在 `hits`（rank=4，score=0.9583），而 `RULE-RAD-06` 仍被挡——解锁是逐条的；
  5. `scene_pack` 随之把全文放进可引用区，`archive_code` 的 `codes_csv` 收了它，主持人这才允许引用；
  6. **同一句叙述各跑一次「裁决校验与记忆文案」**：解锁前 `RULE-RAD-05` 被就地换成「一段还没被写下来的规矩」并记进 `narration_redacted`；解锁后同一句话原样保留、`narration_redacted` 为空——闸门不误伤。
- **B. 结局链条**：`END-RAD-01` 在档案包 ending 区 → 主持人判定达成后 `endings_next=END-RAD-01`、`stage_next=ended`、本轮新解锁的 `RULE-RAD-06` 并入 `revealed_next`、记忆文本写成「…当前场景=scn_seaside_radio：…；解锁封存规则 RULE-RAD-06；达成结局 END-RAD-01」；模型编造 `END-XXX-99` 时 `endings_next` 为空、**不封盘**。记忆 `kind` 的优先级用四种本轮事实逐一目视确认：违规+解锁+结局→`violation`，解锁+结局→`unlock`，只有结局→`ending`，都没有→`progress`。
- **C. 真写回再读回**：把 A/B 两轮 `remember_body` 的 scope 换成本次新建的 scope 真 POST 给 PowerContext（200/201），再用 DSL 的「解析记忆条目」节点合并两路响应 → `memory_count=2`、`active_scene_id=scn_seaside_radio`。**这条 `active_scene_id` 就是「新开一个会话、说『继续』也不会被重派」的依据**：会话变量是空的，场景从记忆标签里解出来。
- **判读 / 边界**：这一条证明的是**后端 + 提交文件里那段节点代码**的行为，不是云端一次真实运行的记录。云端那一半现在由 S10 的 `s12/s13/s14` 补上（带 `workflow_run_id` 的逐节点记录）；控制台截图仍未补，见 §未通过项 3。

## S10 · 云端整套重采：9 个取证场景 / 24 轮，全钉在发布版 `375be8cc` 上

```bash
cd dify && python3 tools/cloud_run_trace.py s3_same_conversation   # streaming SSE → evidence/cloud_run_*.txt
python3 tools/cloud_publish_parity.py                               # 把运行记录钉回提交文件
```

每份 `evidence/cloud_run_*.txt` 都是同一套结构：文件头 `sha256(dsl)` + `git HEAD`，每轮一段（`workflow_run_id` → 逐节点 `status/elapsed` → 关键输出字段 → **泄露自检行** → 玩家可见回复原文）。自检行三个量分别是：本轮仍封存的编号、回复里真出现的封存编号、回复里的区间写法（`RULE-XX-01 到 06` 这类），另加「塌成固定文案」这一运行级稳定性指标。

| 取证场景 | 轮数 | 实际结果（能从记录里指认的） |
| --- | --- | --- |
| `s3_same_conversation` | 2 | 第 1 轮 `taste=…声音…` 派到电台（`scene_source=fresh`），第 2 轮把音量从 3 拧到 1 又开口哼 → `cite_line` 出现 RULE-RAD-01、RULE-RAD-02、OMEN-RAD-01、END-RAD-01，`violations_next=RULE-RAD-01,RULE-RAD-02`，随后 `endings_next=END-RAD-01`、`stage_next=ended` 当场封盘。**同会话第二轮读到第一轮写入的预兆，说明会话变量与记忆两条链路都在环** |
| `s10_reroll_words` | 4 | 「继续，别换活」「接着讲上一局」都**没有**重派：`scene_source=conversation`、`scene_id=scn_seaside_radio` 一路带下去，同一幕往前推；第 4 轮「换个场景，重新派单吧」才 `scene_source=reroll`，请求体里带上 `exclude_scene_id=scn_seaside_radio`，于是这一轮派到 `scn_convenience_store`（难度 5）。**四轮无一塌兜底**——这一条正是上一版塌方的那四轮 |
| `s11_cross_conversation` | 3 | 第 3 轮是**全新会话**、玩家只说「继续上一局，别重派」：`scene_source=memory` + `active_scene_id=scn_convenience_store`（场景从记忆标签解出来，不是重派）；叙述照抄记忆旧账「上一回你在同一间店里，OMEN-KIO-02 和 OMEN-KIO-03 一起亮过」并跟一句「我念完了，没说我信」（铁律 6）。**同一轮泄露闸门真触发**：模型想点 RULE-KIO-06，出文本被换成「一段还没被写下来的规矩」，`narration_redacted=RULE-KIO-06`，自检行「回复里出现的封存编号=无」 |
| `s12_unlock_positive` | 2 | 第 2 轮玩家做出 ITEM 条文写明的用途动作 → `revealed_next=RULE-RAD-05`、`mem_kind=unlock`，状态条 `🔓 已解锁封存：RULE-RAD-05`（行动轮解锁在这版上是通的） |
| `s13_unlock_cloud` | 4 | 从零走到封盘：第 2 轮解 RULE-RAD-05，第 3 轮解 RULE-RAD-06，第 4 轮补上 END-RAD-03 的台词子句「把网收了」→ `endings_next=END-RAD-03`、`stage_next=ended`。第 2/3 轮自检行报「回复里出现的封存编号=RULE-RAD-05 / RULE-RAD-06」——那是**刚刚解锁成功的当轮**，编号已进白名单，属预期放行不是泄露 |
| `s14_ending_bad` | 3 | 第 2 轮三秒空白里哼两声 → `cite_line=本轮引用：RULE-RAD-02、OMEN-RAD-01、END-RAD-01`、`endings_next=END-RAD-01`、`stage_next=ended`、`mem_kind=violation`（坏结局当场兑现，记忆按违规记账）；第 3 轮做尽「断电 + 对着喇叭说『回来』」也只推进剧情，`本轮引用：无`，好结局 `END-RAD-03` 一个字没提。这一场景同时暴露了 `stage` 只写不读：第 2 轮 `ended`、第 3 轮回 `playing`，见 §未通过项 5 |
| `s15_taste_scene` | 1 | S2 的线上重采：口味原话进 `taste`（不含第三人称概括）→ seekdb 命中《礁石村的深夜电台》 |
| `s16_difficulty_cap` | 2 | 同一口味只差难度表述：「最简单的」→ `difficulty=1` 派到 `scn_office_elevator`（难度 1），「中等」→ `difficulty=3` 派到 `scn_seaside_radio`——纯语义检索给不出这种区分，这是 `where difficulty_max` 参与的证据 |
| `s17_leak_probe` | 3 | 玩家明着要「从 RULE-RAD-01 念到 06」「只说个范围」：三轮的自检行都是「回复里出现的封存编号=无；回复里的区间写法=无」，且后端在未解锁时根本不下发条文（主持人手里只有编号+危险度） |

- **自检行的两种「非无」都要会读**：`s12` 第 2 轮、`s13` 第 2/3 轮报「回复里出现的封存编号=RULE-RAD-05 / 06」而 `本轮封存中` 同一栏还列着它——那是**刚刚解锁成功的当轮**，编号当场进白名单、`narration_redacted` 为空，属预期放行（自检比的是本轮档案块里的旧清单）。真正拦下一次的是 `s11` 第 3 轮：那一轮 `narration_redacted=RULE-KIO-06`、自检「回复里出现的封存编号=无」——抹掉之后才出文本，且 `s17` 三轮玩家明着索要范围，自检三格全「无」。
- **这一轮重采的直接动因**：上一版 `ad854abe` 的 `s10` 第 3、4 轮整轮塌成兜底句（那两份取证文件已被本轮重采覆盖，症状原文不在仓库里；形状复刻是用 SSE 事件流逐节点抓的，原始输出含推理正文，按约定不提交）。复刻到的那一轮：主持人 LLM 节点 `status=succeeded`、`finish_reason=stop`、`len(text)≈2.8 KB`，内容顺序是**成对闭合的思考标签 + 一段纯中文主持文本 + 模型自己拼的状态条，裁决 JSON 一个字节都没有**——状态条长这样（这就是玩家上一轮看到的排版，被当成了范本）：

  ```text
  ---
  🪪 工号 `A-…` ｜ 🎬 礁石村的深夜电台 ｜ 第 3 轮 ｜ 心情 uneasy
  📁 档案引用：本轮引用：RULE-RAD-01、OMEN-RAD-01、OMEN-RAD-02（4 条规则可读｜封存 2 条）
  ⛔ 累计违规： ｜ 🏁 结局收集： ｜ 🔓 已解锁封存：
  🧠 已写入 PowerContext 记忆（kind=`progress`，…）
  ```

  根因是该节点开着对话记忆窗口（`memory.window` enabled，`size=10`），模型把**自己上一轮的玩家可见输出**当续写范本，而那份排版里根本没有 JSON 契约。两处一起改：主持人节点关掉该窗口（跨轮状态只走会话变量 + 记忆摘要 + 本轮档案块），`host_code` 加 `_prose` 兜底（只在思考标签成对闭合时取正文当叙述，截掉状态条，仍过封存闸门）。重发布后 9 个场景 24 轮无一塌方。
- **代价照实说**：关掉窗口后，「就按你上一条给的第一个选项来」这类**指代主持人自己上一句**的说法没有原文可查，可能接不准；玩法从第一句起就要求玩家把动作说全，兜底文案那句「请把动作说得更具体些」也是这个意思。
- **稳定性不是承诺**：`deepseek-flash` + `temperature=0.85` 下同一种坏形状仍可能出现别的变体；这一版保证的是「出现了也不把推理正文吐给玩家」，不是「永远不出现」。24 轮里 0 次塌方、0 次编号泄露，是对这一版的一次抽样，不是压测结论。

## 未通过 / 未完成项（如实说明）

1. **「跨会话必定翻旧账」仍不是形式保证，但失忆的口子已经在云端实测堵上。** 根因还是本实例 PowerContext 只有 fts 召回（S4 的召回矩阵给出量化）。仓库 DSL 改成 `prepare` + `entries/list` 两路并读去重，顺序这一路不受措辞影响。**这一版已经在线上跑过**：`s11` 第 3 轮是全新会话、会话变量全空，玩家只说「继续上一局，别重派」，`scene_source=memory` + `active_scene_id` 从记忆标签里解出场景，叙述照抄了记忆里的 OMEN-KIO-02/03（见 S10 表第三行）。剩下的两点没变：档案袋很长时 `list` 只取最近 6 条，更早的旧账仍可能进不了上下文；铁律 6 本身仍是提示层约束（记忆非空必须点出一条历史、只能照抄原文），V3c 证明缺条时它选择照实说而不是编造，但那不是硬隔离。
2. **曾经的决定性缺口（提交的 DSL 比线上发布版新）已经闭合：线上跑的就是提交文件。** `evidence/cloud_publish_parity.txt` 第 2–5 节给出：逐节点 (type,title) 20 个全一致、20 条连线集合一致、28 个变量引用集合一致、实质差异字段 0 个；环境变量只有名字相同、值不同（本地占位 vs 隧道地址，域名按约定打码）；第 5 节把 9 份 `cloud_run_*.txt` 的 `workflow_id` 全部对上控制台 `workflows/publish` 返回的 `id=375be8cc…`，每份文件头的 `sha256(dsl)` 也与当前提交文件一致。`evidence/cloud_export/app.yaml` 现在是**这次发布之后**从云端回导的打码副本，不再是导入当时的旧快照。云端回导带来的无关差异仍只有两处：`ARCHIVE_TOP_K` 的 `value_type` 被规范化为 `integer`、`features.file_upload` 补齐云端默认子字段——它们落在比对工具的噪音字段里，不计入差异。
3. **控制台运行截图仍未补。** 逐节点执行记录、`workflow_run_id`、发布版本号三者已经咬合（S10 + 上一条），但「打开控制台指着那一轮给人看」的截图还没留。补法：控制台 → 该应用 → 日志 → 用 `workflow_run_id` 定位那一条运行，连节点耗时面板一起截图存进 `evidence/screenshots/`。早期列的那批「线上就是旧版」「运行证据回溯不到提交文件」这类老缺口，现在只剩这一条没补。
4. S2–S5 每轮 14–26 s（S10 的 24 轮主持人节点 `completion_tokens` 375–4229 不等），主要是两个 LLM 节点 + 两个后端串行的结果，没有做流式或并行优化。
5. **`stage=ended` 只写不读——「封盘」是记账意义上的，不是锁幕。** 结局当轮主持人确实宣布、`endings_next` 收下、记忆 `kind=ending`（S10 的 `s3/s13/s14` 都在云端复现了），但流程图上没有任何分支读 `conversation.stage`，所以玩家在同一会话里继续说话时，主持人仍在同一幕往下讲、结局只往「🏁 结局收集」里累加。`s14` 第 3 轮就是这个形状（第 2 轮 `ended`，第 3 轮 `playing`）。这一条按玩法取舍保留（结局是可收集的，不是一局一锁），但 RUNBOOK 里「当场封盘」那句话容易被读成锁幕，已按实改写。
6. **主持人输出契约仍可能被模型绕开，闸门也不是字面全覆盖。** `_prose` 兜住的是已知的那一种坏形状（成对闭合思考 + 纯文本 + 自己拼的状态条），`temperature=0.85` 下别的变体仍可能出现；本轮 24 轮 0 塌方是一次抽样，不是压测结论。长期稳定性压测没做。**字符串级闸门只拦两样**：裁决字段里的编号（必须来自本轮检索命中，编不出新的）和叙述里的**未解锁封存编号**（就地换成「一段还没被写下来的规矩」）。模型若在叙述里随手写一个白名单外的**可读**编号（`s14` 第 3 轮就出现过：正文点了 NPC-RAD-02，`cite_line` 却是「本轮引用：无」——引用栏只登记裁决数组里的编号），网关不会替它兜，那是提示词层面的约束。玩家仍可拿编号去「查档案」核对，核对不上就是主持人说错了，这条口子是玩法故意留的。
7. **工号不是凭据、令牌是单一共享密钥**：威胁模型表里那两条「生产应改成每客户端一令牌 / 工号绑定会话身份，本项目未做」仍未做，见 README §安全边界。多端适配与多语言也没做。
