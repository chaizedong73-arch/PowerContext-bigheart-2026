# Qoder 赛题作战包（Build with Qoder）

本目录是「用 Qoder 做一个让学习、科研和工作更高效的项目」（20 分）的作战包。
**所有项目代码必须在 Qoder（桌面版/IDE/JetBrains 插件/CLI 任一）中编写**，本目录只提供 PRD、分阶段提示词、验证样例与提交材料模板，保证开发过程真实发生在 Qoder 并可留痕。

## 项目方向（已确认）

**个人复习教练（Web 应用）**——把零散的学习资料变成「可检索的知识库 + 会记着你薄弱点的复习伙伴」。

- **seekdb**：负责知识检索——导入的资料切块后用嵌入式 pyseekdb 建索引，复习时做「向量 + 元数据过滤 + 关键词」混合检索并给出处引用。
- **PowerContext**：负责学习者记忆——把错题、薄弱点、复习偏好、进度作为 Memory 长期保存，下次打开时通过 `/v1/context/prepare` 取回历史上下文，生成「接着上次继续」的复习计划。

## 用户操作总流程

1. **注册 Qoder CN 账号**（个人账号，不要用企业账号），领取 2000 Credits（约 1 个月 Pro）。
2. **在 Qoder 中新建项目**，指向本仓库本地克隆（`2026-competition-ecosystem` 分支），项目代码建在 `qoder/app/` 下。
3. **按 `prompts/` 顺序逐段执行**：每个阶段复制一段提示词到 Qoder Agent，跑通并自测后再进入下一阶段。每完成一个阶段，**截图 Qoder 对话**保存到本地（后面要交）。
4. **按 `VALIDATION.md` 执行验证样例**，边跑边留存证据（截图/日志）。
5. **按 `SITES.md` 用 Qoder Sites 发布项目网站**，补齐网站必备内容。
6. **按 `RUNBOOK.md` 模板补全运行说明**（含 seekdb / PowerContext 部署接入步骤）。
7. 提交材料清单见 `SITES.md` 第 4 节。

## 目录说明

```
qoder/
├── README.md        # 本文件：作战包使用指南
├── PRD.md           # 产品需求文档（产品定位、功能清单、架构、两组件分工）
├── prompts/         # 六阶段 Qoder 提示词（内嵌已核实的 API 用法，防幻觉）
│   ├── phase-1-scaffold.md        # 阶段1：项目脚手架
│   ├── phase-2-seekdb.md          # 阶段2：资料导入 + seekdb 混合检索
│   ├── phase-3-powercontext.md    # 阶段3：PowerContext 学习者记忆
│   ├── phase-4-review-ui.md       # 阶段4：复习流程与前端 UI
│   ├── phase-5-validate.md        # 阶段5：调试验证（跑通验证样例）
│   └── phase-6-sites.md           # 阶段6：Sites 发布准备
├── VALIDATION.md    # 验证样例：初始数据 + 输入 + 步骤 + 预期结果 + 技术发挥作用记录法
├── RUNBOOK.md       # 运行说明模板（提交时补全）
└── SITES.md         # Qoder Sites 发布步骤与提交材料清单
```

## 关键红线（提交前自查）

- [ ] 主要代码确实在 Qoder 中编写，留有对话截图/录屏 + Qoder 账号 UID。
- [ ] seekdb 与 PowerContext **真实发挥作用**，且有可展示的调用记录（见 `VALIDATION.md` 第 4 节）。
- [ ] 项目网站通过 **Qoder Sites** 发布，且设置为「任何人都可访问」。
- [ ] 仓库中**不含任何真实 API Key**（一律走环境变量）。
- [ ] 运行环境注意：pyseekdb 的 wheel 仅支持 **Linux x86_64**，后端请在 WSL2 或 Linux 中运行（详见 `RUNBOOK.md`）。
