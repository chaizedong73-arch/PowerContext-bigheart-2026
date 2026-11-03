# 截图清单（site-material/screenshots/）

本目录用于归集**本应用真实运行截图**，作为提交材料「演示」与「技术发挥作用」的取证。

> 说明：Qoder 内置浏览器在本次会话中处于隐藏视口状态（`visibilityState=hidden`），
> Agent 无法直接抓取像素截图。下列截图需在本地运行应用后**手动截取**，
> 文件名请按下表命名，截取后放入本目录即可（站点首页「演示」区已用内置数据
> 真实渲染了同等界面，截图为额外加分取证）。

## 运行应用（截图前）

```bash
# WSL2 Ubuntu 内
cd /mnt/c/Users/chaizedong/OneDrive/ドキュメント/Trae-CN/temp_20261101_01/PowerContext-bigheart-2026
export SEEKDB_PATH=/home/chaizedong/recallloop-data/seekdb
export POWERCONTEXT_URL=http://127.0.0.1:8000
export POWERCONTEXT_SCOPE_ID=scp_4y5bypa0y6njp369ykn7btggvt
# 先确保 PowerContext Server 已运行：powercontext server run
~/recallloop-venv/bin/uvicorn main:app --app-dir qoder/app --host 127.0.0.1 --port 8100
```

浏览器打开 `http://127.0.0.1:8100/`，状态胶囊显示「● 已连接后端」即为真实模式。

## 待截取清单

| 文件名 | 画面 | 取证点 |
| --- | --- | --- |
| `01-about-landing.png` | 「关于」首页整页 | 六项必备内容（简介/问题/功能/演示/仓库/体验入口） |
| `02-library.png` | 资料库页，已导入 os-basics.md(11 块) + study-notes.md(5 块) | V1 seekdb 索引 |
| `03-ask-citations.png` | 问答页提问「seekdb 和 PowerContext 在本项目中如何分工？」，Top-5 依据卡带出处与向量/关键词信号 | V2 seekdb 三路混合检索 |
| `04-ask-filtered.png` | 同一问题 + 来源过滤 os-basics.md，结果全部来自该文件 | V2 结构化过滤 |
| `05-review-cards.png` | 复习页挖空卡（如「＿＿＿ 是蚂蚁集团自研的分布式关系数据库」答案 OceanBase） | 出题质量 |
| `06-review-selfrate.png` | 自评「会/模糊/不会」按钮 + 本次统计（正确率 40%） | V3 自评 |
| `07-progress-ready.png` | 进度页：上次复习 chip=ready、今日清单薄弱来源优先、薄弱点列表 | V4 跨会话续接 |
| `08-progress-empty.png` | 换全新 scope 后进度页显示「首次使用，还没有历史记录」(status=empty) | V4 对照组 |
| `09-retrieval-log.png` | 打开 `qoder/app/data/retrieval_log.jsonl`，可见 vector/keyword 得分 | seekdb 证据 |
| `10-memory-log.png` | 打开 `qoder/app/data/memory_log.jsonl`，可见 remember/sources/search/prepare 调用 | PowerContext 证据 |

## 截图技巧

- Windows：`Win + Shift + S` 框选，粘贴到画图/截图工具另存为 PNG。
- 浏览器整页：F12 → `Ctrl + Shift + P` → 输入 `Capture full size screenshot`。
- 日志文件：用编辑器打开后截取含关键字段的若干行即可，注意**不要截到任何 token**。
