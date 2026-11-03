# 项目交接记录（HANDOFF）

> 更新时间：2026-11-04　分支：`2026-competition-ecosystem`（fork：chaizedong73-arch/PowerContext-bigheart-2026）
> 本文件记录当前项目状态、已完成工作与后续待办，供后续会话/协作者接手。

## 1. 当前状态一览

| 事项 | 状态 | 对应提交 |
| --- | --- | --- |
| 赛题四（PDF 证据页检索 Recall@20）修复 | ✅ 已完成并推送 | `513816e7` fix(pdf): overhaul evidence-page retrieval recall |
| MiniMax 赛题（Mock 链路修复 + 真实 e2e 运行证据） | ✅ 已完成并推送 | `9c85a5f4`、`b47310d0` |
| Qoder 赛题「Build with Qoder」作战包 | ✅ 已完成并推送 | `6f822857` |
| Qoder 项目应用本体（复习教练 RecallLoop） | ⬜ 待在 Qoder 中开发 | 见 `qoder/prompts/` |

## 2. Qoder 赛题（当前活跃任务）——下一步操作

赛题：用 Qoder 开发「让学习、科研和工作更高效的项目」（20 分）。硬性要求：项目必须用 Qoder 开发、实际使用 seekdb / PowerContext 至少一种、网站经 Qoder Sites 发布（公开可访问）。

**已定方案**：个人复习教练（Web 应用）。seekdb（pyseekdb 嵌入式）负责知识检索；PowerContext（本地 Server + HTTP API）负责学习者记忆与跨会话续接。两者同用，分工说明已写入 `qoder/PRD.md` 第 5 节。

**用户待办（全部在 Qoder 侧完成，代码不在本仓库直接手写）**：

1. 注册 Qoder CN 个人账号（勿用企业账号），领取 2000 Credits。
2. 在 Qoder 打开本仓库（分支 `2026-competition-ecosystem`），按顺序执行 `qoder/prompts/phase-1` → `phase-6` 六段提示词；代码生成到 `qoder/app/`。每阶段截图 Qoder 对话存档。
3. 按 `qoder/VALIDATION.md` 跑 V1–V4 并回填执行记录。
4. 按 `qoder/SITES.md` 用 Qoder Sites 发布（可见范围选「任何人都可访问」），补齐提交材料清单。
5. 提交前自查：无真实 API Key、六项网站内容齐全、Qoder 账号 UID 只填提交表单不进仓库。

## 3. 关键技术事实（防踩坑）

- pyseekdb wheel 仅支持 **Linux x86_64**：后端在 WSL2/Linux 运行（本机 WSL `~/pc-venv` 已验证过 pyseekdb）。
- seekdb 数据库目录必须本地文件系统，不能放 `/mnt/c` 等挂载路径。
- pyseekdb 已验证用法参考：`minimax/ctxsdd/obstore.py`（Client(path=) / HNSWConfiguration / upsert / query 签名）。
- PowerContext HTTP 端点已核实（`src/powercontext/http/_generated/schema.py`）：`POST /v1/scopes`、`/v1/sources/content`、`/v1/memory/remember`、`/v1/memory/search`、`/v1/context/prepare`；显式 Memory 写入无需配置推理提供方。
- PowerContext 安装：`uv tool install --force "powercontext[cli,server]==1.2.0"`，启动 `powercontext server run`。

## 4. 仓库环境约定

- 工作区：`C:\Users\chaizedong\OneDrive\ドキュメント\Trae-CN\temp_20261101_01\PowerContext-bigheart-2026`。
- Git 提交身份（本仓库专用，勿改全局配置）：
  `git -c user.name="chaizedong73-arch" -c user.email="248132368+chaizedong73-arch@users.noreply.github.com" commit ...`
- 提交信息用临时文件 `-F`（Windows PowerShell 不支持 heredoc）。
- 工作树中若干 `M` 文件为 CRLF 换行符噪音（`git diff --stat` 无实质内容），可忽略，勿误提交。

## 5. 参考材料

- Qoder 赛题原文：`https://oceanbase.yuque.com/org-wiki-obtech-vh7w9r/hc3pio/uygu3273xzlrcmrm`
- 作战包入口：`qoder/README.md`；产品定义：`qoder/PRD.md`；运行说明模板：`qoder/RUNBOOK.md`；发布与材料清单：`qoder/SITES.md`。
