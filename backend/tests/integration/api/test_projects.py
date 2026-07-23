"""B-03 Project API 集成测试。

验证项目 CRUD 端点：
- 创建、查询、列表、更新
- 404 处理
- 参数校验
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.integration
@pytest.mark.asyncio
class TestCreateProject:
    """创建项目。"""

    async def test_create_project_returns_201(self, async_client: AsyncClient) -> None:
        """创建项目返回 201 + ProjectResponse。"""
        response = await async_client.post(
            "/api/v1/projects",
            json={"title": "足球少年", "target_episode_count": 10},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["title"] == "足球少年"
        assert data["target_episode_count"] == 10
        assert data["status"] == "draft"
        assert "id" in data
        assert "created_at" in data

    async def test_create_project_defaults(self, async_client: AsyncClient) -> None:
        """不传可选字段时使用默认值。"""
        response = await async_client.post("/api/v1/projects", json={})
        assert response.status_code == 201
        data = response.json()
        assert data["title"] == ""
        assert data["target_episode_count"] == 10

    async def test_create_project_title_too_long(self, async_client: AsyncClient) -> None:
        """title 超过 200 字符返回 422。"""
        response = await async_client.post(
            "/api/v1/projects",
            json={"title": "A" * 201},
        )
        assert response.status_code == 422


@pytest.mark.integration
@pytest.mark.asyncio
class TestGetProject:
    """查询项目。"""

    async def test_get_existing_project(self, async_client: AsyncClient) -> None:
        """查询已创建的项目返回 200。"""
        create_resp = await async_client.post(
            "/api/v1/projects",
            json={"title": "查询测试"},
        )
        project_id = create_resp.json()["id"]

        response = await async_client.get(f"/api/v1/projects/{project_id}")
        assert response.status_code == 200
        assert response.json()["title"] == "查询测试"

    async def test_get_nonexistent_project_returns_404(self, async_client: AsyncClient) -> None:
        """查询不存在的项目返回 404 + PROJECT_NOT_FOUND。"""
        response = await async_client.get(
            "/api/v1/projects/00000000-0000-0000-0000-000000000000"
        )
        assert response.status_code == 404
        data = response.json()
        assert data["code"] == "PROJECT_NOT_FOUND"
        assert "request_id" in data


@pytest.mark.integration
@pytest.mark.asyncio
class TestListProjects:
    """列表项目。"""

    async def test_list_projects_pagination(self, async_client: AsyncClient) -> None:
        """分页查询项目列表。"""
        for i in range(3):
            await async_client.post(
                "/api/v1/projects",
                json={"title": f"列表测试 {i}"},
            )

        response = await async_client.get("/api/v1/projects?offset=0&limit=2")
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 2
        assert data["total"] >= 2
        assert data["offset"] == 0
        assert data["limit"] == 2

    async def test_list_projects_default_limit(self, async_client: AsyncClient) -> None:
        """不传分页参数时使用默认值。"""
        response = await async_client.get("/api/v1/projects")
        assert response.status_code == 200
        data = response.json()
        assert data["limit"] == 20
        assert data["offset"] == 0


@pytest.mark.integration
@pytest.mark.asyncio
class TestUpdateProject:
    """更新项目。"""

    async def test_update_title(self, async_client: AsyncClient) -> None:
        """更新项目标题。"""
        create_resp = await async_client.post(
            "/api/v1/projects",
            json={"title": "原标题"},
        )
        project_id = create_resp.json()["id"]

        response = await async_client.patch(
            f"/api/v1/projects/{project_id}",
            json={"title": "新标题"},
        )
        assert response.status_code == 200
        assert response.json()["title"] == "新标题"

    async def test_update_target_episode_count(self, async_client: AsyncClient) -> None:
        """更新目标集数。"""
        create_resp = await async_client.post(
            "/api/v1/projects",
            json={"title": "集数测试"},
        )
        project_id = create_resp.json()["id"]

        response = await async_client.patch(
            f"/api/v1/projects/{project_id}",
            json={"target_episode_count": 20},
        )
        assert response.status_code == 200
        assert response.json()["target_episode_count"] == 20

    async def test_update_nonexistent_returns_404(self, async_client: AsyncClient) -> None:
        """更新不存在的项目返回 404。"""
        response = await async_client.patch(
            "/api/v1/projects/00000000-0000-0000-0000-000000000000",
            json={"title": "不存在"},
        )
        assert response.status_code == 404
        assert response.json()["code"] == "PROJECT_NOT_FOUND"
