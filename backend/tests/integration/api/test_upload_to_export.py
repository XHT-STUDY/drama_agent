"""导入 → 导出端到端测试 (G-06，两条路径)。

覆盖阶段 G Exit Gate：
- Outline 文件能进入创作流程：上传 Outline → action=import（route=create）
  → action=create_script（config.upload_id 注入上传文本作为创作输入）
  → 导出 StoryBible / 大纲 / 剧本;
- 完整剧本文件能进入评估流程：上传完整剧本 → action=import（route=evaluate，
  full_script 时由确定性转换持久化 script_draft）→ action=evaluate
  → 导出评估报告。

全部走 HTTP API（runs.py 的 Worker 装配，含 _resolve_upload_text 接线），
使用 FakeLLM（导入分类 / 创作 / 评估均注册 golden fixtures）。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from httpx import AsyncClient

# 模糊大纲：有"第X集"标记但无场景/对白 → 规则不命中 → LLM 兜底（outline golden）
_OUTLINE_TEXT = (
    "大纲：第1集主角被青训队抛弃，第2集低谷期遇到伯乐教练，"
    "第3集组建草根球队开始逆袭，第4集首战告捷重拾信心。"
)
# 强剧本结构：场景≥2 + 对白≥5 → 规则命中 full_script（不调 LLM）
_FULL_SCRIPT_TEXT = (
    "第1场 训练场（日）\n"
    "教练：你被开除了。\n"
    "林峰：为什么？\n"
    "教练：因为你不够强。\n"
    "\n"
    "第2场 宿舍（夜）\n"
    "林峰：我绝不放弃。\n"
    "室友：可你已经没有机会了。\n"
    "林峰：那就证明给他们看。\n"
)


@pytest.mark.integration
@pytest.mark.asyncio
class TestUploadToExport:
    """两条导入路径端到端。"""

    async def _create_project(self, async_client: AsyncClient, title: str) -> str:
        resp = await async_client.post("/api/v1/projects", json={"title": title})
        assert resp.status_code == 201, resp.text
        return str(resp.json()["id"])

    async def _upload_txt(
        self,
        async_client: AsyncClient,
        project_id: str,
        filename: str,
        text: str,
    ) -> str:
        files = {"file": (filename, text.encode("utf-8"), "text/plain")}
        resp = await async_client.post(
            f"/api/v1/projects/{project_id}/uploads", files=files
        )
        assert resp.status_code == 201, resp.text
        return str(resp.json()["id"])

    async def _start_run(
        self,
        async_client: AsyncClient,
        project_id: str,
        action: str,
        config: dict[str, Any],
    ) -> str:
        resp = await async_client.post(
            f"/api/v1/projects/{project_id}/runs",
            json={"action": action, "config": config},
        )
        assert resp.status_code == 202, resp.text
        return str(resp.json()["run_id"])

    async def _wait_completed(
        self, async_client: AsyncClient, run_id: str, max_tries: int = 80
    ) -> None:
        status = "queued"
        for _ in range(max_tries):
            await asyncio.sleep(0.2)
            resp = await async_client.get(f"/api/v1/runs/{run_id}")
            status = resp.json()["status"]
            if status in ("completed", "failed"):
                break
        assert status == "completed", f"Run {run_id} 未完成: {status}"

    async def _export(
        self,
        async_client: AsyncClient,
        project_id: str,
        kinds: list[str],
    ) -> dict[str, Any]:
        resp = await async_client.post(
            f"/api/v1/projects/{project_id}/exports",
            json={"kinds": kinds, "format": "markdown"},
        )
        assert resp.status_code == 202, resp.text
        run_id = resp.json()["run_id"]
        await self._wait_completed(async_client, run_id)
        arts = await async_client.get(
            f"/api/v1/projects/{project_id}/artifacts?type=export_file"
        )
        items = arts.json()["items"]
        assert items, "导出后应存在 export_file Artifact"
        assert isinstance(items[0], dict)
        return items[0]

    # ---- Path 1: Outline → 创作 → 导出 ----

    async def test_outline_upload_to_creation_to_export(
        self, async_client: AsyncClient
    ) -> None:
        """上传 Outline → 导入分类 route=create → 创作（上传文本为输入）→ 导出。"""
        project_id = await self._create_project(async_client, "Outline 端到端")
        upload_id = await self._upload_txt(
            async_client, project_id, "outline.txt", _OUTLINE_TEXT
        )

        # 1. 导入分类 → route=create
        import_run = await self._start_run(
            async_client, project_id, "import", {"upload_id": upload_id}
        )
        await self._wait_completed(async_client, import_run)

        # 2. 创作：config.upload_id 注入上传文本（不传 options.user_input）
        create_run = await self._start_run(
            async_client, project_id, "create_script", {"upload_id": upload_id}
        )
        await self._wait_completed(async_client, create_run)

        # 3. 导出 StoryBible / 大纲 / 剧本 + 下载验证内容
        art = await self._export(
            async_client, project_id, ["story_bible", "outline", "script"]
        )
        dl = await async_client.get(
            f"/api/v1/exports/{art['id']}/download",
            params={"project_id": project_id},
        )
        assert dl.status_code == 200, dl.text
        text = dl.content.decode("utf-8")
        assert "世界观与人物设定" in text, "StoryBible 应进入导出"
        assert "十集大纲" in text, "大纲应进入导出"
        assert "第 1 集剧本" in text, "剧本应进入导出"

    # ---- Path 2: 完整剧本 → 评估 → 导出 ----

    async def test_full_script_upload_to_evaluate_to_export(
        self, async_client: AsyncClient
    ) -> None:
        """上传完整剧本 → 导入分类 route=evaluate + script_draft 入库 → 评估 → 导出。"""
        project_id = await self._create_project(async_client, "完整剧本端到端")
        upload_id = await self._upload_txt(
            async_client, project_id, "第一集.txt", _FULL_SCRIPT_TEXT
        )

        # 1. 导入分类 → route=evaluate，且 full_script 持久化 script_draft
        import_run = await self._start_run(
            async_client, project_id, "import", {"upload_id": upload_id}
        )
        await self._wait_completed(async_client, import_run)

        arts = await async_client.get(
            f"/api/v1/projects/{project_id}/artifacts?type=script_draft"
        )
        scripts = arts.json()["items"]
        assert len(scripts) == 1, "完整剧本应持久化为 script_draft"
        assert scripts[0]["status"] == "valid"
        content = scripts[0]["content"]
        assert len(content["scenes"]) == 2
        assert content["scenes"][0]["location"] == "训练场"

        # 2. 评估（对已入库的导入剧本逐集评估）
        eval_run = await self._start_run(async_client, project_id, "evaluate", {})
        await self._wait_completed(async_client, eval_run)

        # 3. 导出评估报告 + 下载验证内容
        art = await self._export(async_client, project_id, ["evaluation"])
        dl = await async_client.get(
            f"/api/v1/exports/{art['id']}/download",
            params={"project_id": project_id},
        )
        assert dl.status_code == 200, dl.text
        text = dl.content.decode("utf-8")
        assert "评估报告" in text, "评估报告应进入导出"
        assert "开头钩子" in text, "评估维度标签应进入导出"
