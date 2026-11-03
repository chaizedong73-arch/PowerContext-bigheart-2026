# Qoder 开发记录（site-material/qoder-log/）

赛题硬性要求：开发全程在 Qoder 中完成，并提交**对话截图/录屏 + Qoder 账号 UID**作为开发实践取证。

## 一、对话截图（每阶段至少 1 张）

本项目按 6 个阶段提示词（`qoder/prompts/phase-1..6`）在 Qoder Agent 中逐步生成。
请从 Qoder 对话历史中截取每个阶段的代表性画面，按下表命名后放入本目录。

| 文件名 | 阶段 | 截图内容建议 |
| --- | --- | --- |
| `phase-1-scaffold.png` | 阶段 1 脚手架 | Agent 生成 FastAPI + config + 静态单页壳的对话 |
| `phase-2-seekdb.png` | 阶段 2 seekdb | 生成 embedder/store、三路混合检索、导入资料的对话 |
| `phase-3-powercontext.png` | 阶段 3 PowerContext | 生成 memory.py、scope/remember/search 的对话 |
| `phase-4-review-ui.png` | 阶段 4 复习+UI | 生成 review.py 出题与四页前端的对话 |
| `phase-5-validate.png` | 阶段 5 验证 | WSL2 中跑通 V1–V4、回填 VALIDATION.md 的对话 |
| `phase-6-sites.png` | 阶段 6 站点 | 生成站点首页与材料归集的对话 |

> 加分项：可再录一段「从提示词到代码生成再到运行」的连续录屏，命名 `qoder-walkthrough.mp4`，
> 链接填到 `../demo-video.md`。

## 二、Qoder 账号 UID（重要：不要写进仓库）

- 取法：Qoder 设置 / 个人中心查看账号 UID。
- **UID 只填在提交表单，绝不写入本仓库任何文件**（避免个人信息进入公开代码库）。
- 本目录不放含 UID 的文件；如需本地备忘，请写在仓库之外。

## 三、自查

- [ ] 6 张阶段对话截图已放入本目录（或录屏链接已记录）。
- [ ] 仓库内 `git grep -iE "sk-|api[_-]?key\s*=\s*['\"]"` 结果为空（无真实密钥）。
- [ ] 仓库内不含 Qoder 账号 UID。
