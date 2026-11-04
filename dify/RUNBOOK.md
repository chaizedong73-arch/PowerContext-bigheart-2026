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
| PowerContext | 1.2.0，`powercontext server run` | HTTP API，默认 127.0.0.1:8000 |
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
curl -s localhost:8011/archive/health                       # {"ok":true,...}
curl -s -H 'X-Archive-Token: change-me-local-dev' \
     'localhost:8011/archive/search?query=电梯+声音&kinds=rule&top_k=3'
```

不带令牌或令牌错误必须返回 401；这条是「access control 生效」的证据，见 [tools/validate_dsl.py](tools/validate_dsl.py) 与 `VALIDATION.md`。

数据单独灌也可以：`~/recallloop-venv/bin/python seed.py --force`。语料是 [archive/data/scenes.json](archive/data/scenes.json)，自己写的场景直接加进去重灌即可。

## 3. 安装并启动 PowerContext

```bash
~/recallloop-venv/bin/pip install powercontext==1.2.0
~/recallloop-venv/bin/powercontext server run      # 默认 127.0.0.1:8000
curl -s localhost:8000/health                       # 或 /v1/scopes 探活
```

本实例没有配 embedding provider，`/v1/context/prepare` 走 fts 召回——这意味着查询措辞要和写入时的用词接近（见 §7 局限）。
`tools/run_pc_demo.sh` 会演示「同工号两次建档返回同一个 scope_id」的幂等性。

## 4. 导入 Dify DSL

1. Dify → 工作室 → **创建应用 → 通过 YAML/DSL 导入**（自建版是「导入 DSL 文件」）。
2. 选择 [dsl/midnight_archive_chatflow.yml](dsl/midnight_archive_chatflow.yml)。
3. 应用类型是 `advanced-chat`（Chatflow）。导入后应看到 **18 个节点 / 18 条连线**；若节点上出现 ⚠ 不兼容，按 §5 重新绑定即可。
4. 左上「保存」→「发布」。

云端版导入成功后可以「导出 DSL」反向核对：本次导出的 `.ifpkg`（zip 内 `manifest.yaml` + `app.yaml`）留在 [evidence/cloud_export/](evidence/cloud_export)，证明手写 DSL 被云端原样接受。

## 5. 重新绑定模型（换账号/换版本必做）

工作流只有两个 LLM 节点用到模型：

| 节点 | 作用 | 建议参数 |
| --- | --- | --- |
| **意图与关键词抽取** | 把玩家原话归一成 `intent / keywords / action / reroll / difficulty` | temperature **0.2**（分类要稳） |
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

## 7. 试玩

1. 打开应用 → 右侧「运行」→ 「工号」填一个编号（例如 `A-1101`，会被规范化后用作 PowerContext scope 的幂等键）。
2. 第一句直接说口味：`今晚给我派个活，我想要有声音、后背发凉但别太吓人的。`
3. 之后每轮描述你要做的一件事。回复末尾的状态条给出「场景 / 回合 / 心情 / 本轮引用条目 / 累计违规 / 结局 / 已解锁封存」，可以直接核对。
4. 说「查档案」看条文原文；说「换场景」重新派单；换个会话、用同一个工号回来，主持人会翻你的旧账。

## 8. 离线校验（不联网也能跑）

```bash
python3 dify/tools/capture_fixtures.py    # 从本地后端抓真实响应到 dify/capture/
python3 dify/tools/validate_dsl.py        # 结构 + 变量引用 + 代码节点编译 + 真实响应回放
```

当前输出：`nodes=18 edges=18 / distinct variable references = 27 / 0 errors, 0 warnings`（日志见 [evidence/validator_stdout.txt](evidence/validator_stdout.txt)）。
校验器会故意喂「玩家原话里带引号/换行/花括号」的对立输入，确认 `json.dumps` 序列化不被拼坏；也检查恶意/残缺 JSON 走兜底分支不抛异常。

## 9. 已知局限（如实说明）

- **两个后端必须同时在线。** 任一 HTTP 节点失败会中断整轮，Dify 只在画布日志里报错，对话端表现为「运行失败」。这是「组件真在干活」的副作用，没有做降级壳。
- **quick tunnel 域名会变**（§6）。要稳定请换命名隧道或反向代理。
- `ARCHIVE_TOKEN` 是**本地占位令牌**，只用于证明访问控制生效，别把它当成公网安全方案；对外暴露时请自行加 TLS/鉴权。
- **PowerContext 走 fts 召回，对措辞敏感。** 写入用「违反」，查询用「违规」可能召回不到；本项目已在 `host_code` 里把违规摘要作为固定词写回，但跨会话「翻旧账」的命中率不是 100%。
- **封存规则只有后端侧是硬保证。** `sealed=true` 且未解锁的条目在 API 层（`_unlock_filter`）被剔出 `hits`，`scene_pack` 只把编号与危险度写进 `sealed_locked`，条文与标题都不下发；因此主持人拿不到被封存条文的任何一个字。剩下的一环是模型：它仍可能概率性地在叙述里提到某个封存编号（例如用「RULE-ELV-01 到 05」这种区间说法裹进去）。提示词已明令禁止区间表述，无法做到形式化保证。
- **语料只有 5 个场景 / 90 条条目**，玩久了会重复；扩充只需往 `data/scenes.json` 加场景并重灌。
- **会话状态存在 Dify `conversation` 变量里**，单会话串行；同工号开多个会话时，违规计数各自独立，只有长期记忆通过 PowerContext 共享。
- 「裁决」本质是 LLM 读档案块后自己判定，不是规则引擎；同一情境可能给略有差别的后果。所有被引用的编号都写进了回复，玩家可以去「查档案」核对——这是本玩法故意留的口子。
- 未做移动端适配与多语言。
