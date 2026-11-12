# Qoder 开发记录（多AI交接桥 · 赛题提交）

> 赛题硬性要求：项目主要开发工作在 Qoder 中完成并保留过程记录。本文件列出各阶段工具归属与 Qoder 会话证据槽位；截图由账号所有者从 Qoder 会话存档导出补齐。

## 一、阶段与工具归属

| 阶段 | 内容 | 工具 | 证据 |
| --- | --- | --- | --- |
| v0.4.x（2026-11-04~11-06） | **四段管线核心**：①采集②归一化③seekdb 三路检索+PowerContext④简报生成器；评测集与打分器；增量懒补（行偏移水位线）；int8-ONNX 内存实测（证伪并留档）；MCP 轻壳协议层验证 | **Qoder** | vault 维护日志逐条可溯（多AI交接桥（v2）/维护日志.md，2026-11-04~11-06 各条）；截图槽位 §二 |
| v0.5.0~v0.6.0（2026-11-07） | 控制台（管线四环节可检查软件）；AI 工作台原生模组化 | ZC（ZCode） | vault 维护日志 |
| v0.7.0~0.7.4（2026-11-11~12） | light 零 torch 后端（简报全链内存 1458MB→472MB）；实时性时间窗检索与识别词会话归属；跨项目硬验收；GitHub 公开发布 | ZC | vault 维护日志 |
| v0.7.5（2026-11-12） | 简报管线接入 PowerContext 记忆复用（生成前读历史 → 落盘后 remember+handoff 工件；协议一致性 mock 实测双轮通过） | ZC | VALIDATION.md V5 |
| **本次赛题适配（2026-11-12）** | **V1–V5 验证真实跑通并回填**（VALIDATION §5：light/seekdb 双模式导入、检索、简报、MCP tools/call；PowerContext 真实 Server 双轮复用）；修复真实 Server 暴露的两处契约 bug（state/citations、remember 去重空 citation）；回归 94.6 不变。**Qoder Sites 展示页已发布并公开**：`https://handoff-bridge-g913y85jmem.qoder.zone` | **Qoder** | VALIDATION.md §5 + `demo-data/validation/` + 展示站 |

## 二、Qoder 会话截图槽位（提交前补齐）

| # | 覆盖环节 | 状态 |
| --- | --- | --- |
| 1 | v0.4.x 四段管线与评测集开发 | ⬜ |
| 2 | v0.4.x MCP 轻壳协议验证 | ⬜ |
| 3 | 本次 Sites 展示页开发 | ⬜ |
| 4 | 本次验证执行与回填 | ⬜ |

Qoder 账号 UID：⬜（按赛题要求只填提交表单，不写入仓库）

## 三、诚实声明

- v0.5.0 之后的内存优化、实时性修复与发布由 ZCode 接力完成——跨 agent 接手并延续工作，正是本项目要解决的场景本身；接手过程有 vault 维护日志逐条留痕。
- 四段管线核心、评测体系、MCP 协议层为 Qoder 阶段产出，构成项目的主要功能基座。
- 2026-11-12 Qoder 阶段适配：按 `VALIDATION.md` 在隔离数据目录实跑 V1–V5 并回填执行记录；期间修复了 PowerContext 记忆复用在真实 Server 上的两处契约 bug（此前仅 mock 验证）。所有产物均为合成演示数据，不含真实会话。
- 所有阶段（无论哪个 agent 开发）均按同一规约留档：作者标记 + 维护日志六项，可逐条回溯。
