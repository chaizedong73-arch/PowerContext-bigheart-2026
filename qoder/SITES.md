# Qoder Sites 发布与提交材料清单

## 1. 发布前提

- 阶段 5 验证全过、阶段 6 站点素材齐备。
- Qoder 为**个人账号**登录（Qoder Sites 个人版站点可设为「任何人都可访问」；企业账号/企业版不支持对外发布，勿用企业身份）。

## 2. 发布步骤

1. 在 Qoder 中打开本项目，进入 Sites / 发布面板。
2. 选择发布来源：优先尝试直接托管 `qoder/app/`（若平台支持运行型应用）；否则选择静态托管 `qoder/app/static/`（演示模式站点，见 phase-6 提示词第一节）。
3. 可见范围选择 **「任何人都可访问」（Public）**——赛题硬性要求，不要选私有/邀请制。
4. 记录生成的站点 URL，浏览器无痕窗口验证可匿名访问。
5. 在站点页核对六项内容：项目简介 / 解决的问题 / 核心功能 / 演示（视频或截图）/ 代码仓库链接 / 体验入口。

> 发布后的任何代码更新都需要在 Qoder 中重新发布一次，最终提交以最后一次发布的版本为准。

## 3. Qoder 开发记录（提交硬性材料）

| 材料 | 取法 |
| --- | --- |
| 对话截图 | 每个阶段（1–6）至少 1 张 Qoder Agent 对话截图，存 `qoder/site-material/qoder-log/` |
| 录屏（可选加分） | 录一段「从提示词到代码生成再到运行」的连续录屏 |
| Qoder 账号 UID | Qoder 设置/个人中心查看；**只填在提交表单，不写进仓库** |

## 4. 提交材料总清单（提交前逐项打勾）

- [x] **网站 URL**：https://recallloop-coach-g913y85jmem.qoder.zone （Qoder Sites，public，2026-11-07 核对线上内容与仓库 `qoder/app/static/` 一致、匿名可访问）
- [ ] **代码仓库**：`https://github.com/chaizedong73-arch/PowerContext-bigheart-2026`（分支 `2026-competition-ecosystem`，代码在 `qoder/app/`）
- [ ] **运行说明**：`qoder/RUNBOOK.md`（含 seekdb 部署/接入步骤、PowerContext 部署/接入步骤）
- [ ] **验证样例**：`qoder/VALIDATION.md`（初始数据 + 输入 + 步骤 + 预期结果 + 技术发挥作用记录，执行记录已回填）
- [ ] **Qoder 开发记录**：对话截图/录屏 + 账号 UID（⬜ 截图待归集进 `site-material/qoder-log/`，见其 README）
- [ ] **使用效果数据**：使用人数/次数、提效对比、反馈（附访问统计截图，见 VALIDATION.md 第 4 节）（◐ PV=4 已回填文档，统计截图与自用记录待补）
- [x] **无真实 API Key**：`git grep -iE "sk-|api[_-]?key\s*=\s*['\"]"` 命中的两行均为 `ask-citations`/`ask-filtered` 文件名误报，实查为空
- [x] 网站可匿名访问且六项内容齐全

## 5. 评分维度自查（最后过一遍）

| 评分项 | 自查点 |
| --- | --- |
| 实用价值 | 网站第一屏能否 10 秒讲清解决什么问题 |
| 使用效果 | 有无真实使用数据/对比记录 |
| 完成度体验 | 无 Key 演示模式全流程可点通 |
| 技术结合 | 网站与验证样例都能看出 seekdb 三路检索、PowerContext 跨会话续接 |
| Qoder 开发实践 | 截图/录屏/UID 齐全 |
| 工程质量 | RUNBOOK 可照跑、验证样例可复现、无密钥泄漏 |
