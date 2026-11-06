# 《午夜档案馆》运行说明

从零到能玩：装组件 → 灌数据 → 起两个后端 → 开隧道 → 导入 Dify → 绑模型 → 改两个环境变量 → 发布 → 试玩。
所有示例值都是占位值，不含任何真实 Key、令牌或主机名。

---

## 1. 环境与版本

| 项 | 本次实测版本 | 说明 |
| --- | --- | --- |
| 操作系统 | Windows 11 26200 + WSL2 (Ubuntu) | `pyseekdb` 的 wheel 只有 Linux x86_64 / macOS，Windows 原生跑不了，所以后端放在 WSL2 |
| Python | 3.14.4（venv `~/recallloop-venv`） | 3.10+ 均可 |
| Dify | 云端版（DSL `version: 0.1.5` 手写导入；云端回导为 `0.7.0` `.ifpkg`） | 自建版 1.x 同样可用，见 §4 |
| seekdb | `pyseekdb` 1.4.0.post1，**嵌入式模式** | 不额外起数据库进程，索引落在本地目录 |
| PowerContext | 1.2.0，`powercontext server run` | HTTP API，默认 127.0.0.1:8000；**存储后端是 SQLite + FTS5**（`~/.local/share/powercontext/powercontext.db`），与 seekdb 是两个并列后端，记忆不落 seekdb |
| Embedding | sentence-transformers 6.2.0 / torch 2.15.0，模型 `paraphrase-multilingual-MiniLM-L12-v2`（384 维，离线） | 加载失败自动降级为确定性哈希向量，玩法不受影响 |
| Web 框架 | fastapi 0.162.0 / uvicorn 0.54.0 | |
| 模型 | `deepseek-flash`（Dify 市场 deepseek provider，chat 模式） | 两个 LLM 节点都用它，可换成任一 chat 模型 |
| 隧道 | `cloudflared` 2026.10.7（quick tunnel，免 root） | 或任意内网穿透 / 反向代理 |

依赖清单在 [archive/requirements.txt](archive/requirements.txt)。

## 2. 安装并启动 seekdb 检索后端

```bash
# 2.1 建环境（已有 venv 可跳过）
python3 -m venv ~/recallloop-venv
~/recallloop-venv/bin/pip install -r dify/archive/requirements.txt

# 2.2 配置：全部占位值
cd dify/archive
cp .env.example .env
#   必改的只有两项：ARCHIVE_TOKEN（自己随便定一个字符串）、ARCHIVE_PORT
#   ARCHIVE_SEEKDB_PATH 要是 Linux 可写路径

# 2.3 启动（首次运行会自动建库并灌入 5 个场景 / 90 条档案条目）
./run.sh
```

自检：

```bash
curl -s localhost:8011/archive/health | python3 -m json.tool   # {"ok":true,...,"count":90}

# 检索类端点都是 POST + JSON 体（GET 会拿到 {"detail":...}）
curl -s -X POST localhost:8011/archive/search \
     -H 'Content-Type: application/json' -H 'X-Archive-Token: change-me-local-dev' \
     -d '{"query":"我回头看向反光板","keywords_csv":"反光板,回头","scene_id":"scn_office_elevator","kinds_csv":"rule","top_k":3}'

# 场景正文来自库里那条 kind=scene 记录，不是直读 scenes.json
curl -s -X POST localhost:8011/archive/scene_pack \
     -H 'Content-Type: application/json' -H 'X-Archive-Token: change-me-local-dev' \
     -d '{"scene_id":"scn_office_elevator"}'
```

不带令牌或令牌错误必须返回 401；这条是「access control 生效」的证据，见 [tools/validate_dsl.py](tools/validate_dsl.py) 与 `VALIDATION.md`。

`/archive/search` 的返回里有三个字段是玩法的支点，改后端时别顺手删掉：

- `hits`：已过 API 层封存门的命中，未解锁的封存条文一个字都不在里面；
- `sealed_gated`：仍封着的编号与危险度（`from=hit` 是三路命中后被挡下的，`from=scene_rules` 是本场景 where 查出来的），**只有 `code/danger/state`**，这是主持人唯一的解锁候选集；
- `scene_endings`：带 `scene_id` 时整条交回的本场景结局条文。结局不封存，但行动轮 top_k 撞不撞得中全看措辞；主持人手里没有触发条件全文时，「条件凑齐才给结局」的铁律等于永远不许封盘。

数据单独灌也可以：`~/recallloop-venv/bin/python seed.py --force`。语料是 [archive/data/scenes.json](archive/data/scenes.json)，自己写的场景直接加进去**重灌**即可——运行期读的是库，改完文件不重灌是不会生效的，这反过来也证明正文来自库里。


## 3. 安装并启动 PowerContext

```bash
~/recallloop-venv/bin/pip install powercontext==1.2.0
~/recallloop-venv/bin/powercontext server run      # 默认 127.0.0.1:8000
curl -s localhost:8000/health                       # 或 /v1/scopes 探活
```

本实例没有配 embedding provider，`/v1/context/prepare` 只有 fts 召回——命中与否随措辞变化。
量化数据用 [tools/pc_recall_matrix.py](tools/pc_recall_matrix.py) 复现（输出即 [evidence/pc_recall_matrix.txt](evidence/pc_recall_matrix.txt)）：
一条写着「违反 RULE-RAD-02」的记忆，用玩家原话「继续」「我刚才是不是犯规了」去 prepare 都是 0 命中，
而 `/v1/memory/entries/list` 恒返回全部条目。所以 Chatflow 两路都读，去重后合成同一块记忆，
「翻旧账」和「续上原来那一局」才不靠措辞运气。
`tools/run_pc_demo.sh` 会演示「同工号两次建档返回同一个 scope_id」的幂等性。

存储后端可以直接验：

```bash
file ~/.local/share/powercontext/powercontext.db      # SQLite 3.x database
sqlite3 ~/.local/share/powercontext/powercontext.db ".tables"   # pc_scopes pc_memory_entry_* pc_memory_entry_fts …
```

## 4. 导入 Dify DSL

1. Dify → 工作室 → **创建应用 → 通过 YAML/DSL 导入**（自建版是「导入 DSL 文件」）。
2. 选择 [dsl/midnight_archive_chatflow.yml](dsl/midnight_archive_chatflow.yml)。
3. 应用类型是 `advanced-chat`（Chatflow）。导入后应看到 **20 个节点 / 20 条连线**；若节点上出现 ⚠ 不兼容，按 §5 重新绑定即可。
4. 左上「保存」→「发布」。**改了仓库里的 DSL 就必须重导入 + 重发布**，否则运行证据回溯不到提交文件——`tools/validate_dsl.py` 会把本地 DSL 和云端回导快照逐节点、逐连线、逐变量引用比对，不一致就报错，不给过。

云端版还有一条更省事、也是本项目实际采用的重导入路径：**不必删应用重导入**，把仓库 DSL 的 `workflow.graph` 整图 POST 回控制台草稿，再点发布即可（改文件 → 重导入 → 重发布是一条命令的事，且杜绝手改草稿导致线上线下再次分叉）：

```bash
# 1) 从提交的 DSL 生成整图载荷（graph / features / conversation_variables），产物写在仓库外
python3 tools/build_draft_payload.py <repo>/dify <scratch>       # → <scratch>/draft_payload_full.json
# 2) 浏览器里在控制台同源发三个请求（需要登录态与 x-csrf-token，载荷由本机 :8900 供文件）
#    GET  /console/api/apps/<app_id>/workflows/draft                       → 取 hash
#    POST /console/api/apps/<app_id>/workflows/draft
#         {graph: <仓库整图>, features/conversation_variables: <线上草稿原样>, hash: <上一步的 hash>}
#         ⚠ 这个接口不接受 environment_variables（多传会 400 extra_forbidden），
#           隧道地址那五项保持在草稿里不动即可。
#    POST /console/api/apps/<app_id>/workflows/publish
# 3) GET .../workflows/publish 与 GET .../export?format=yaml&type=app 取回线上快照
#    （zip 导出在云端版返回 422，所以拿的是 yaml 单文件），再跑 tools/cloud_publish_parity.py 咬合。
```

本项目用这条路径把「换单排除上一幕」「行动轮解锁」「行动轮结局条文下发」三次改动依次推上线，发布版本号与逐节点证据见 `evidence/cloud_publish_parity.txt` 与 `evidence/cloud_run_*.txt`。

⚠ 登录态过期时这三个请求全部返回 401 `Invalid Authorization token`，而 `document.cookie` 里 `__Host-csrf_token` 会一起消失。把编排页刷新一次（同一 URL 重新导航）令牌会重新种回来，再发就行——不用重新登录，也不用删应用。

云端版导入成功后可以「导出 DSL」反向核对：本次导出的 `.ifpkg`（zip 内 `manifest.yaml` + `app.yaml`）留在 [evidence/cloud_export/](evidence/cloud_export)，证明手写 DSL 被云端原样接受。⚠ 后来云端改用「整图 POST 草稿 + 重发布」更新，`.ifpkg` 的 zip 导出接口返回 422，所以 `app.yaml` 已经跟着最新版本重新导出，而 `manifest.yaml` 还是导入当时那份（它记的是旧 `app.yaml` 的 sha256，对不上是新常态，别拿它当版本证据）。

## 5. 重新绑定模型（换账号/换版本必做）

工作流只有两个 LLM 节点用到模型：

| 节点 | 作用 | 建议参数 |
| --- | --- | --- |
| **意图与关键词抽取** | 把玩家原话归一成 `intent / keywords / action / difficulty`（**不输出 reroll**：换单由「解析分类结果」在词面上判定，见 §7） | temperature **0.2**（分类要稳） |
| **怪谈主持人（裁决+叙述）** | 依据档案块裁决违规、写叙述 | temperature **0.85**（叙述要活） |

步骤：点开节点 → 模型栏 → 选**你自己账号里可用的 chat 模型** → 保存。
DSL 里预置的是 `langgenius/deepseek/deepseek` + `deepseek-flash`；没有这个 provider 时第一次打开会显示「不兼容」，**只要选一次模型就恢复**，不用改任何连线或变量。

不要用不支持 JSON 输出的推理模型做「意图与关键词抽取」——它要把结构化字段交给代码节点解析；该代码节点已经对坏 JSON、缺字段做了兜底，但分类质量取决于模型本身。

## 6. 配置环境变量与（云端）网络条件

Dify 应用 → 「环境变量」（云端版在编排页右上角），DSL 已带占位值，导入后必须改成真实可达地址：

| 变量 | 占位默认 | 你要填什么 |
| --- | --- | --- |
| `ARCHIVE_BASE_URL` | `http://127.0.0.1:8011` | **云端 Dify 不能访问你的 127.0.0.1**，必须填公网可达的隧道/代理地址，形如 `https://<你的隧道域名>`；自建版且 Dify 与后端同机时填 `http://host.docker.internal:8011` |
| `ARCHIVE_TOKEN` | `change-me-local-dev` | 与后端 `.env` 的 `ARCHIVE_TOKEN` **完全一致**；不一致时 HTTP 节点会拿到 401，整轮中断 |
| `PC_BASE_URL` | `http://127.0.0.1:8000` | 同上，云端需隧道地址 |
| `PC_SCOPE_FALLBACK` | 空 | 兜底 scope；留空表示建档失败就报错（建议留空，便于发现问题） |
| `ARCHIVE_TOP_K` | `6` | 每轮从 seekdb 取回的条目数，4～10 之间 |

免 root 开两条隧道（cloudflared 官方 apt 仓库下载被墙时，从 apt pool 取 deb 再 `dpkg-deb -x` 解出二进制）：

```bash
cloudflared tunnel --url http://localhost:8011   # 输出 ARCHIVE_BASE_URL
cloudflared tunnel --url http://localhost:8000   # 输出 PC_BASE_URL
```

⚠ quick tunnel 的 `*.trycloudflare.com` 域名**每次重启都会变**，变了要同步改 Dify 环境变量并重新发布。

⚠ **进程还在 ≠ 隧道还通。** 实测踩过一次（起后约 50 分钟）：`pgrep` 查得到两个 cloudflared，本地 `127.0.0.1:8000` / `:8011` 都秒回，但走隧道域名的 `curl` 全部 10 s 超时（`exit=28`、HTTP 000）。云端的样子是 `evidence/cloud_run_*.txt` 里 `建立/复用工号档案袋` 或 `取场景档案包(seekdb)` 节点 `status=failed error=500 Internal Server Error`，后面的节点整条不执行、`answer` 为空、每轮都「新开会话」。按 pid 杀掉重开就会拿到新域名——旧域名不会回来。

⚠ **域名与后端的对应关系要实测，别按日志顺序猜。** 两条隧道的域名写在两个不同的 log 里，哪个属于 8000 哪个属于 8011 很容易接反（本仓库作者就反过一次，结果整轮云端全挂在建档节点上）。逐域名打一发指纹请求，只有后端自己有区别：

```bash
for u in https://域名A https://域名B; do
  curl -s -o /dev/null -w "$u /archive/health  HTTP %{http_code}\n"  --max-time 20 "$u/archive/health"   # 只有档案后端 200
  curl -s -o /dev/null -w "$u /v1/scopes       HTTP %{http_code}\n" --max-time 20 -X POST "$u/v1/scopes" -H 'Content-Type: application/json' -d '{}'   # 只有 PowerContext 422（缺 idempotency_key）
done
```

改环境变量不必在页面上手动敲，控制台 API 有独立接口（**整份替换**，要把全部 5 个变量一起发回去，否则未列出的会丢；值是明文字符串，`ARCHIVE_TOP_K` 用 `number`）：

```bash
# 在已登录 cloud.dify.ai 的浏览器里同源调用，需 cookie + x-csrf-token（从 __Host-csrf_token 取）
GET  /console/api/apps/$APP_ID/workflows/draft/environment-variables     # 拿 items（含 id / value_type / description）
POST /console/api/apps/$APP_ID/workflows/draft/environment-variables     # body: {"environment_variables":[...]} → {"result":"success"}
POST /console/api/apps/$APP_ID/workflows/publish                          # body: {} → 生成新的已发布版本号
```

注意 `POST …/draft/environment-variables/{id}` 和 `…/draft/publish` 都**不存在**（404），只有上面三条路径可用。发布后 `GET …/workflows/publish` 返回的那条 `id` 就是 `cloud_publish_parity.py` / `cloud_run_trace.py` 要对的已发布版本号。


## 7. 试玩

1. 打开应用 → 右侧「运行」→ 「工号」填一个编号（例如 `A-1101`，会被规范化后用作 PowerContext scope 的幂等键）。
2. 第一句直接说口味：`今晚给我派个活，我想要有声音、后背发凉但别太吓人的。`
3. 之后每轮描述你要做的一件事。回复末尾的状态条给出「场景 / 回合 / 心情 / 本轮引用条目 / 累计违规 / 结局 / 已解锁封存」，可以直接核对。
4. 说「查档案」看条文原文；换个会话、用同一个工号回来，主持人会翻你的旧账。
5. **换单只在你说出明确的换场景说法时才生效**（「换个场景」「重新派单」「来点新的」…，词面清单在 DSL 的 `REROLL_WORDS`）。说「继续」「接着讲」会留在当前场景——哪怕你新开一个会话、会话变量是空的，也会从档案袋条目里的 `当前场景=<scene_id>` 标签续上原来那一局。这是「解析分类结果 + 组装检索请求体」两个代码节点决定的，不交给模型裁量。**难度同理**：`DIFFICULTY_WORDS` 把你原话里的「最简单 / 中等 / 恐怖」直接钉成后端 `where difficulty_max` 的数值，模型漏抽或抽偏都会被你的原话覆盖（只在派单轮生效，行动轮的「像往常一样」不算）。请求体里的 `scene_source` 会如实标出这一局的场景是从哪来的：`fresh` 开局 / `conversation` 会话内续 / `memory` 跨会话续 / `reroll` 显式换单。
6. **解锁靠做事，不靠报编号。** 状态条的「🔓 已解锁封存」只在你对**道具条文里写明的用途**做出动作那一轮之后才会变（例：`ITEM-RAD-02` 的用途是「核对船号，是判断 RULE-RAD-05 触发条件是否成立的唯一凭据」，`ITEM-RAD-01` 的用途是「执行 RULE-RAD-05 的唯一手段」）。原话里点名道具编号，是为了让主持人把动作对上条文——它仍然只在后端交回的 `sealed_gated` 编号里挑，编不出新的。
7. **结局凑齐条件当场宣布并记账**（状态条「🏁 结局收集」变非空、那一轮 `stage=ended`、记忆 `kind=ending`）。每个场景的结局条文每轮都随检索整条下发，所以撞得中撞不中不再取决于你这一句怎么说；反过来说，条件没凑齐时主持人被禁止透露任何一个字。**「记账」不是「锁幕」**：流程图上没有任何分支读 `conversation.stage`，封盘那一轮之后你再说话，主持人仍在同一幕往下讲，结局只往收集栏里累加（云端 `s14` 第 2 轮 `ended`、第 3 轮又回到 `playing` 就是这个形状）。要的真结局是「收集齐」而不是「一局一锁」，所以保留了这个行为。
8. **结局的触发句里有「台词子句」，光做事不够。** 例：`END-RAD-03` 要「以 ITEM-RAD-02 核对船号 + 用 ITEM-RAD-01 断电执行 RULE-RAD-05 + 用 ITEM-RAD-03 的三分钟解开 RULE-RAD-06」**并且**「最后一夜你说的是『把网收了』」——前三件做齐了主持人仍判 `playing`（这是条文写死的，不是它压盘），补上最后一夜那句话才封盘。取证时这一点直接决定了要跑几轮（`s13` 因此是 4 轮不是 3 轮）。


## 8. 校验与取样脚本

前两条**不联网也能跑**（只读 `capture/` 里的 fixture），后两条要本地后端在线：

```bash
python3 dify/tools/capture_fixtures.py    # 从本地后端抓真实响应到 dify/capture/
python3 dify/tools/validate_dsl.py        # 结构 + 变量引用 + 代码节点编译 + 真实响应回放
python3 dify/tools/pc_recall_matrix.py    # 记忆两路的命中矩阵（要 PowerContext 在线）→ evidence/pc_recall_matrix.txt
python3 dify/tools/reveal_sample.py       # 解锁+结局正向路径（要两个后端在线）→ evidence/reveal_positive_path.txt
```

云端发布之后，用这条抽**运行级证据**（`workflow_run_id` + 逐节点状态与耗时）。App API 的 blocking 响应里没有运行 ID，只有 streaming 的 SSE 事件里才有，所以取证走 streaming：

```bash
export DIFY_API_KEY=...         # 控制台「访问 API」新建，用完撤销；只走环境变量，不落仓库
export DIFY_BASE_URL=https://api.dify.ai/v1
cd dify && python3 tools/cloud_run_trace.py s3_same_conversation   # 或 s10_reroll_words / s11_cross_conversation / s12_unlock_positive / s13_unlock_cloud / s14_ending_bad / s15_taste_scene / s16_difficulty_cap / s17_leak_probe
# ⚠ 重新取证前先把 tools/cloud_run_trace.py 里的工号换一批：档案袋是按工号共享的，
#    沿用旧工号时上一轮解开的封存规则、记下的场景都还在，第 1 回合就少了一道闸，
#    证据不再是「从零走到封盘」。本项目的做法是每重采一次就把 `A-CLOUD-S*` 整体换号。
# 解析器离线自测（不联网）：python3 tools/cloud_run_trace.py --replay <一段真实 SSE 文本> selftest
```

取证一次要连发十几轮请求，`本机 → 代理 → Cloudflare → Dify` 这一段实测会整段抖（`SSL: UNEXPECTED_EOF_WHILE_READING`、隧道 530），所以每一轮只对**网络错误**重试最多三次；HTTP 4xx/5xx 原样抛出，重试次数与最终跑通的轮次都会写进证据文件（`（重试，这一轮最终跑通）…`），不会把抖动藏起来。

输出 `evidence/cloud_run_<场景>.txt`，文件头写上 DSL 的 sha256 与 `git HEAD`，运行记录因此能回溯到提交文件。

两个文件头里，**真正咬死的是 DSL 的 sha256**：`git HEAD` 只是采集时本机分支的偶然而已——只要之后发生过 rebase（本项目就遇过一次：远端 head 领先，把本地两枚提交重新落上去，hash 全换），那行 HEAD 会指向一个本地都找不到的孤儿提交，而证据内容一点没变。核对一份旧证据时先比 sha256，别拿那行 hash 去 `git show`。

每轮还自动写一行**泄露自检**：

```
   泄露自检：本轮封存中=RULE-RAD-05,RULE-RAD-06；回复里出现的封存编号=无；回复里的区间写法=无；塌成固定文案=否
```

前两项直接对着玩家看到的那段回复算：`locked_csv` 里还没解锁的编号，只要有一个原样出现在回复文本里就写出来（`s17_leak_probe` 就是专门拿「把编号从 01 念到 06」「只说个范围」去逼这句话的）；「区间写法」是 `RULE-XX-01 到 06` 这类没带全编号的说法，闸门同样按字符串拦。最后一项「塌成固定文案」盯的是主持人那一轮没吐出裁决、整轮落到兜底句——它是**运行级**的稳定性指标，不靠人肉读日志数。

再把「提交的文件」和「云端真正在跑的版本」咬到一起（云端产物用控制台接口取回，放仓库外）：

```bash
export CLOUD_APP_YAML=/path/outside/repo/cloud_export/app.yaml          # 控制台「导出 DSL」的 .ifpkg 解出来的那份
export CLOUD_PUBLISHED_JSON=/path/outside/repo/cloud_published_workflow.json   # GET /console/api/apps/<id>/workflows/publish
cd dify && python3 tools/cloud_publish_parity.py   # → evidence/cloud_publish_parity.txt + evidence/cloud_export/（隧道域名打码）
```

它做三件事：逐节点/逐连线/逐变量引用比对提交 DSL 与云端导出；比对两边环境变量（值只报不同、不报内容）；把 `evidence/cloud_run_*.txt` 里的 `workflow_id` 对到控制台返回的**已发布版本号**上。三项都过才算「线上跑的就是提交文件」。

当前输出（日志见 [evidence/validator_stdout.txt](evidence/validator_stdout.txt)）：

```
nodes=20 edges=20
distinct variable references = 28
=== host_code[行动轮解锁] locked=['RULE-ELV-04', 'RULE-ELV-06'] -> revealed_next=RULE-ELV-04 kind=unlock
=== host_code[行动轮结局] ['END-ELV-01', 'END-ELV-02', 'END-ELV-03'] -> endings_next=END-ELV-03 stage=ended kind=ending
round-trip vs cloud export: same 20 edges, same 28 variable references, same node titles
0 errors, 0 warnings
```

仓库 DSL 与云端回导快照已经对上（`round-trip` 那行是逐节点/逐连线/逐引用比的结果），所以这里是 `0 errors`。**这条以后仍然有牙齿**：只要仓库文件领先于线上，它就会报 `cloud export node titles differ` / `variable references differ` 并把 `nodes/edges` 差异列出来——所以「0 errors」本身就是「线上跑的就是提交文件」的持续证明，改完 DSL 不重导入就会红。

其余检查（结构、28 个变量引用可解析、每个代码节点能编译）都通过。校验器还会：
- 把「继续 / 接着讲 / 换个场景 / 重新派单吧 / 给我来点新的」逐句喂给「解析分类结果」，断言只有词面明确换单的那几句 `reroll=1`；
- 同一节点再收一遍**难度**：「难度只要最简单的」→1、「难度中等」→3、「越恐怖越好」→5、「难度中等，别太恐怖」→3（先说出口的那一档优先，贴着否定词的说法不算），而行动轮叙述里的「像往常一样」不得变成难度上限。这条断言是因为云端实测同一个「难度中等」被模型时抽 3 时抽 0，抽 0 那一轮后端拿不到 `difficulty` 过滤，直接派去了难度 5 的便利店；
- 「组装检索请求体」的 `scene_source` 四种来源各断言一次（`fresh` 开局 / `conversation` 续会话 / `memory` 跨会话续局 / `reroll` 显式换单），免得开局回合被标成「重派」；
- 用 `capture/prepare.json` 与 `capture/entries_list.json` 两路真实响应回放「解析记忆条目」，断言 **fts 召回为空时，顺序读档案袋这一路必须单独把条目拼进记忆块并解出场景**；
- 用 `capture/search.json`（行动轮的真实响应）回放「拼装档案块」：`sealed_gated` 交回的编号必须落进 `locked_csv` 且**不带标题**、不进 `codes_csv`；再把它喂给「裁决校验与记忆文案」，断言解锁真的被放行（`revealed_next` 等于该编号、`kind=unlock`）——这条钉的是「行动轮结构性不可能解锁」那个缺陷；
- 同一份响应里的 `scene_endings` 必须**全部**进入 `codes_csv` 且不重复计，随后断言行动轮宣布的结局能通过闸门（`endings_next` 收下、`stage_next=ended`、记忆 `kind=ending`）——钉的是「行动轮拿不到结局触发条件、永远封不了盘」；
- 断言档案块不会一边下发「可引用的规则」一边报「本轮没有命中：规则」（这段以前算错，每轮把五类全列一遍，等于自相矛盾地指示主持人别引用刚交给它的条文）；
- 故意喂「玩家原话里带引号/换行/花括号/控制字符」的对立输入，确认 `json.dumps` 序列化不被拼坏；恶意或残缺 JSON 走兜底分支不抛异常；
- 裁决 JSON 被 `max_tokens` 截断、或叙述里带没转义的引号时，逐字段抠回叙述与编号数组（云端实测过一次 `completion_tokens=2240` 的截断），并且抠回来的内容**不得**复活模型的推理文本；
- **模型压根不吐 JSON、改成照着自己上一轮的玩家可见文本续写**这一种坏形状也各断言一次（上一发布版 `ad854abe` 的 `cloud_run_s10` 第 3 轮实测：状态条都拼全了，裁决一个字节没有；形状与逐节点定位过程写在 `VALIDATION.md` S10，那份取证文件已被本轮重采覆盖，现在仓库里的 `s10` 是修好之后的干净记录）：`_prose` 只在思考标签**成对闭合**时取正文当叙述，把模型自己拼的 `🪪 工号…` 状态条截掉，封存编号照旧过同一道闸门；思考被截断时仍然只回固定文案。断言要求这两种情况的叙述里既没有 `🪪`/`已写入 PowerContext`，也没有推理标记与「我不能…」那句内心独白，且 `narration_redacted` 必须记上被抹掉的编号；
- 断言叙述里出现未解锁的封存编号时被就地抹掉（`narration_redacted` 记下编号），而**已解锁**的编号不被误抹。

## 9. 已知局限（如实说明）

- **两个后端必须同时在线。** 任一 HTTP 节点失败会中断整轮，Dify 只在画布日志里报错，对话端表现为「运行失败」。这是「组件真在干活」的副作用，没有做降级壳。
- **嵌入式 seekdb 只有一条连接，已改成串行 + 断连重连。** 早期版本让 FastAPI 的线程池共用同一条连接，跑了一两个小时后实测打出 `SeekdbError: Server has gone away failed: code=2006`：写路径（`scene_pack`）直接 HTTP 500 中断整轮，读路径更阴——`hybrid_search` 三条通道各自 `except` 把死连接吞成「本轮没有命中」，玩家看到的是主持人凭空失忆。现在 `_lock` 是 `RLock`、取数全程持锁，另加 `_retry_dead()` 命中 2006 就重建集合重试一次（`archive/seekdb_store.py`）。串行意味着并发吞吐有限，这是嵌入式单连接的固有代价，不是优化过的方案。
- **quick tunnel 域名会变**（§6）。要稳定请换命名隧道或反向代理。
- `ARCHIVE_TOKEN` 是**本地占位令牌**，只用于证明访问控制生效，别把它当成公网安全方案；对外暴露时请自行加 TLS/鉴权。
- **PowerContext 的 `prepare` 走 fts 召回，对措辞敏感**（本实例没配 embedding provider，见 §1 与 `tools/pc_recall_matrix.py`）。「继续」「我刚才是不是犯规了」这类原话在 fts 上实测 0 命中，所以本项目把记忆改成**两路并读**：`prepare` 按语义捞 + `entries/list` 按写入顺序取最近 6 条，去重合并进同一块。顺序这一路不受措辞影响，跨会话续场景靠它兜底；但 `list` 只取最近几条，档案袋很长时更早的旧账仍可能进不了上下文。
- **封存条文有两道闸。** 后端侧是硬的：`sealed=true` 且未解锁的条目在 API 层（`_unlock_filter`）被剔出 `hits`，`scene_pack` 只下发编号与危险度，条文和标题都不出网关——主持人拿不到被封存条文的任何一个字。模型侧的泄露（叙述里出现未解锁编号，含「RULE-ELV-01 到 05」这类区间写法）由「裁决校验与记忆文案」节点做**字符串级替换**：命中未解锁编号就换成「一段还没被写下来的规矩」，并把编号记进 `narration_redacted`，校验器有对应断言。这消掉了「编号从叙述里漏出去」这一条；仍不保证模型不会用**不含编号的描述**暗示封存内容——那需要它先知道内容，而内容根本没下发。
- **结局条文每轮都在主持人上下文里，防剧透靠的是提示词而不是网关。** 为了让「达成结局」在行动轮也能判定，`/archive/search` 会把本场景的结局整条交回（`scene_endings`），档案块里标的是「条件全文，不得提前向玩家透露」。换来的是封盘可靠性，代价是主持人如果违反铁律 5，确实可能提前说破结局——它手里一直有那份文本。封存规则不是这样：那两类条文在未解锁时物理上不出网关，模型想泄露也没有内容可泄露。
- **语料只有 5 个场景 / 90 条条目**，玩久了会重复；扩充只需往 `data/scenes.json` 加场景并重灌。
- **会话状态存在 Dify `conversation` 变量里**，单会话串行；同工号开多个会话时，违规计数各自独立，只有长期记忆通过 PowerContext 共享。
- **主持人节点不复读原始对话（该节点的对话记忆窗口是关的）。** 这个窗口一度开着 `window=10`，云端实测能把整轮打塌：模型照着**自己上一轮的玩家可见文本**（带 `🪪 工号…` 那套状态条排版）续写，裁决 JSON 一个字节都不吐——上一发布版 `ad854abe` 的 `cloud_run_s10` 第 3 轮就是这么塌的，当时那一轮的回复只剩兜底句（形状、逐节点定位与修复见 `VALIDATION.md` S10；旧取证文件已被本轮重采覆盖）。现在跨轮状态只有三个来源：Dify 会话变量（场景 / 违规 / 结局 / 已解锁 / 回合 / 阶段）、每轮从 PowerContext 读回的条目摘要（`当前场景=` 标签就在里面）、以及本轮档案块。换来的是输出契约稳定、每轮少几千 prompt token、注入面从「整段对话历史」缩到「本轮原话 + 记忆摘要 + 档案条文」。代价照实说：**指代主持人自己上一句**的说法（「就按你上一条给的第一个选项来」）没有原文可查，可能接不准——所以玩法从第一句起就要求玩家把动作说全，兜底文案那句「请把动作说得更具体些」也是这个意思。真要逐句衔接可以把窗口重新打开，但得先解决状态条被模仿这件事。
- 「裁决」本质是 LLM 读档案块后自己判定，不是规则引擎；同一情境可能给略有差别的后果。所有被引用的编号都写进了回复，玩家可以去「查档案」核对——这是本玩法故意留的口子。
- 未做移动端适配与多语言。
