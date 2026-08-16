# DramaAgent 演示指南（Demo）

> 面向新开发者的**固定可复现**演示流程。全部使用 FakeLLM（离线、免费、确定性），
> 一次完整 Demo（10 集大纲 + 前 3 集剧本 + 评估 + 修订 + 连续性 + 导出）约 3 分钟。
> 真实 LLM 演示见 §4（需自行配置 API Key，属人工 smoke，不计入自动化验收）。

## 0. 前置

```bash
make install
cp .env.example .env      # 本地默认即可，无需真实 LLM Key
make up                   # PostgreSQL + Redis
make doctor               # 环境健康
```

## 1. 自动化演示（推荐）

一条命令跑完整链路 E2E（FakeLLM + 低分场景，含前端工作台全流程）：

```bash
make e2e REPEAT=1
```

覆盖：空项目 → 创作（Idea→StoryBible→10 集大纲→前 3 集剧本）→ SSE 进度 → 刷新 →
内容检查 → 评估 → 自动修订最低分集 → Diff → 导出下载。隔离基础设施（端口 5433/6380），
跑完自动清理，不影响开发库。

验收强度版：`make e2e REPEAT=5`（连续 5 次，H-07/I-05 验收标准）。

## 2. 手动工作台演示（可交互）

### 2.1 启动

```bash
cd backend && APP_ENV=test uv run uvicorn app.main:create_app --factory --port 8000 &
cd frontend && NEXT_PUBLIC_API_BASE=http://localhost:8000/api/v1 pnpm dev &
```

打开 http://localhost:3000

### 2.2 步骤（对应 MVP 主链路）

1. **项目列表** → 点「+ 创建项目」，输入标题（如「足球少年逆袭」）。
2. **输入 Idea**（创作页）→ 粘贴一段中文创作想法，集数选 3 → 「开始创作」。
   - SSE 进度面板实时显示 normalize → story_bible → outline → write_episode 节点。
3. **工作台** → 查看 StoryBible / 分集大纲 / 剧本各集。
4. **刷新页面** → 状态保留（PostgreSQL 为唯一事实源，Artifact 不可变版本）。
5. **评估** → 逐集评分；低分场景下自动修订最低分集（最多 1 轮）。
6. **Diff** → 查看修订前后版本差异（版本模型，绝不原地覆盖）。
7. **导出中心** → 导出 Markdown / DOCX，浏览器下载。

## 3. 导入演示（可选）

- 上传 TXT / DOCX（≤10 MB）→ 自动分类（idea / outline / script）→ 路由到创作或评估链路。
- 典型素材见 `knowledge/` 目录样例。

## 4. 真实 LLM 演示（人工 smoke，需付费 Key）

```bash
# 编辑 .env 填写 LLM_API_BASE / LLM_API_KEY / 各模型名
make up
cd backend && APP_ENV=local uv run python scripts/evaluate_rubric_smoke.py
```

或在工作台按 §2.2 走一遍（真实模型延迟远高于 FakeLLM，属预期；
I-01 重试 / 预算 / 取消、SSE 进度、诊断接口均对真实链路生效）。
**注意**：真实调用会产生 Token 费用；CI 与自动化测试禁用真实 LLM。

## 5. 验收清单（Demo 结束核对）

- [ ] 全链路一次成功，SSE 进度逐步推进
- [ ] 刷新后状态保留，无数据丢失
- [ ] 修订产生**新版本**（原 Artifact 未覆盖）
- [ ] Markdown / DOCX 导出文件可打开，中文正常
- [ ] `GET /runs/{id}/diagnostics` 可查节点时间线与 LLM 统计
- [ ] 若配置了 MCP：外部工具注册生效；未配置则主流程零影响
