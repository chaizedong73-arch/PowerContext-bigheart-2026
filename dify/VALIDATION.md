# 《午夜档案馆》验证样例与运行证据

每条样例都写清「初始状态 → 输入 → 预期 → 实际结果 → 证据位置」，并且只挑**能证明 seekdb / PowerContext 真的在干活**的行为来验，不验「界面能不能打开」。

## 0. 共用初始状态

| 项 | 状态 |
| --- | --- |
| seekdb 后端 | `archive/run.sh` 已启动，`GET /archive/health → {"ok":true,...}`；`pyseekdb` 嵌入式实例，库内 **5 个场景 / 90 条条目**（每场景 6 规则 + 3 道具 + 2 NPC + 3 预兆 + 3 结局 = 17 条，外加场景行本身） |
| 场景难度分布 | 电梯 1｜电台 2｜自习室 3｜绿皮车 4｜便利店 5（每场景恰好 2 条 `sealed=true` 规则） |
| PowerContext | `powercontext server run` @ 127.0.0.1:8000，v1.2.0；该实例未配 embedding provider，`/v1/context/prepare` 走 fts 召回 |
| Dify | 云端 advanced-chat，18 节点 / 18 连线，两个 LLM 节点均绑 `deepseek-flash`（chat），已发布并生成 App API Key |
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
- **证据**：samples 文件 V3 / V3b / V3c 段（含 3 个不同的 conversation_id）。

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
- **证据**：[evidence/archive_probe.txt](evidence/archive_probe.txt) §2–4；断言固化在 [tools/validate_dsl.py](tools/validate_dsl.py)（封存编号出现在可引用集合、或带《标题》出现，即报错）。

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

- **覆盖**：18 节点 / 18 连线；27 个不同变量引用全部有真实来源；所有代码节点可编译；用真实 seekdb / PowerContext 响应回放 `archive_code`、`memory_code`、`host_code`、`req_code`；对立输入（原话含引号、换行、花括号、残缺 JSON、502 HTML、逗号串冒充数组）都必须走兜底而不是抛异常；封存编号若进入可引用集合或带《标题》出现即报错；与云端回导快照做链路级核对。
- **当前结果**：
  ```
  nodes=18 edges=18
  distinct variable references = 27
  round-trip vs cloud export: same 18 edges, same 27 variable references, same node titles
  0 errors, 0 warnings
  ```
  （完整日志 [evidence/validator_stdout.txt](evidence/validator_stdout.txt)）
- **历史作用**：S5 的「口味污染」缺陷（第三人称概括混进 `taste` 导致派单跑偏）就是在这里定位的——把两种 `taste` 字符串各跑一遍检索，结果不同，直接坐实原因。

## 未通过 / 未完成项（如实说明）

1. **V3 的「必翻旧账」没有稳定通过。** 根因是本实例 PowerContext 只有 fts 召回，命中与否取决于措辞（S4 三次取样两种结果）。已把铁律 6 改成「记忆非空必须点出一条历史，且只能照抄记忆原文」，V3c 证明缺条时它选择照实说而不是编造，但**跨会话必定翻旧账**这一点做不到形式保证。
2. **仓库里的 DSL 比线上已发布版本多 3 处收紧，需要重导入+重发布才完全对齐**：铁律 3 的「禁止区间表述」、铁律 6 的「记忆非空必须翻旧账」、封存行不再带标题。
   - `evidence/cloud_export/app.yaml` 是**导入当时**云端回导的快照（version 0.7.0），它的作用是证明手写 DSL 被云端原样接受；它比当前线上版本更早，所以里面还看不到路由 OR 条件与 difficulty 参数。校验器对它做的是**链路级**核对而不是逐字节比对：
     `round-trip vs cloud export: same 18 edges, same 27 variable references, same node titles`。
   - 上述 3 处（以及云端已生效的路由/口味/难度修复）都**不碰 seekdb / PowerContext 的链路与变量绑定**——节点数、连线、变量引用集合、代码节点输出契约完全一致，环境变量与会话变量也没有增删。因此 S1–S8 的行为证据对仓库文件同样成立；重导入并发布后建议重跑 V3/V4 两条。
   - 云端回导带来的无关差异只有两处：`ARCHIVE_TOP_K` 的 `value_type` 被规范化为 `integer`，`features.file_upload` 补齐了云端默认子字段。
3. S2–S5 每轮 14–26 s，主要是两个 LLM 节点 + 两个后端串行的结果，没有做流式或并行优化。
4. 未做多端适配、未做长期运行稳定性压测、未做「解锁道具 → `/archive/reveal`」这条正向路径的线上取样（该接口与令牌、封存过滤都在 S6 里验过，但玩家真正解锁成功的一局没有留在 samples 文件里）。
