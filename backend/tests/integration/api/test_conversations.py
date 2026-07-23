"""B-03 Conversation / Message API 集成测试。

验证会话和消息端点：
- 创建会话、列表会话
- 追加消息、列表消息
- 404 处理、跨项目保护、稳定排序
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.integration
@pytest.mark.asyncio
class TestCreateConversation:
    """创建会话。"""

    async def test_create_conversation_returns_201(self, async_client: AsyncClient) -> None:
        """在项目下创建会话返回 201。"""
        # 先创建项目
        proj_resp = await async_client.post(
            "/api/v1/projects",
            json={"title": "会话测试项目"},
        )
        project_id = proj_resp.json()["id"]

        response = await async_client.post(
            f"/api/v1/projects/{project_id}/conversations",
            json={"title": "第一轮对话"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["title"] == "第一轮对话"
        assert data["project_id"] == project_id
        assert "id" in data

    async def test_create_conversation_project_404(self, async_client: AsyncClient) -> None:
        """在不存在的项目下创建会话返回 404。"""
        response = await async_client.post(
            "/api/v1/projects/00000000-0000-0000-0000-000000000000/conversations",
            json={"title": "测试"},
        )
        assert response.status_code == 404
        assert response.json()["code"] == "PROJECT_NOT_FOUND"


@pytest.mark.integration
@pytest.mark.asyncio
class TestListConversations:
    """列表会话。"""

    async def test_list_conversations_by_project(self, async_client: AsyncClient) -> None:
        """按项目分页查询会话列表。"""
        proj_resp = await async_client.post(
            "/api/v1/projects",
            json={"title": "列表测试项目"},
        )
        project_id = proj_resp.json()["id"]

        for i in range(2):
            await async_client.post(
                f"/api/v1/projects/{project_id}/conversations",
                json={"title": f"会话 {i}"},
            )

        response = await async_client.get(
            f"/api/v1/projects/{project_id}/conversations"
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 2


@pytest.mark.integration
@pytest.mark.asyncio
class TestAppendMessage:
    """追加消息。"""

    async def test_append_message_returns_201(self, async_client: AsyncClient) -> None:
        """追加消息返回 201，sequence 自动递增。"""
        proj_resp = await async_client.post(
            "/api/v1/projects",
            json={"title": "消息测试项目"},
        )
        project_id = proj_resp.json()["id"]
        conv_resp = await async_client.post(
            f"/api/v1/projects/{project_id}/conversations",
            json={"title": "测试对话"},
        )
        conversation_id = conv_resp.json()["id"]

        response = await async_client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"role": "user", "content": "你好，帮我写一个短剧"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["role"] == "user"
        assert data["content"] == "你好，帮我写一个短剧"
        assert data["sequence"] >= 1
        assert data["conversation_id"] == conversation_id

    async def test_append_message_sequence_increments(self, async_client: AsyncClient) -> None:
        """连续追加消息时 sequence 递增。"""
        proj_resp = await async_client.post(
            "/api/v1/projects",
            json={"title": "序号测试"},
        )
        project_id = proj_resp.json()["id"]
        conv_resp = await async_client.post(
            f"/api/v1/projects/{project_id}/conversations",
            json={},
        )
        conversation_id = conv_resp.json()["id"]

        msg1 = await async_client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"role": "user", "content": "第一条"},
        )
        msg2 = await async_client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"role": "assistant", "content": "第二条"},
        )
        msg3 = await async_client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"role": "user", "content": "第三条"},
        )

        assert msg1.json()["sequence"] == 1
        assert msg2.json()["sequence"] == 2
        assert msg3.json()["sequence"] == 3

    async def test_append_message_conversation_404(self, async_client: AsyncClient) -> None:
        """在不存在的会话中追加消息返回 404。"""
        response = await async_client.post(
            "/api/v1/conversations/00000000-0000-0000-0000-000000000000/messages",
            json={"role": "user", "content": "测试"},
        )
        assert response.status_code == 404
        assert response.json()["code"] == "CONVERSATION_NOT_FOUND"

    async def test_append_message_validation(self, async_client: AsyncClient) -> None:
        """缺少必填字段时返回 422。"""
        proj_resp = await async_client.post(
            "/api/v1/projects",
            json={"title": "校验测试"},
        )
        project_id = proj_resp.json()["id"]
        conv_resp = await async_client.post(
            f"/api/v1/projects/{project_id}/conversations",
            json={},
        )
        conversation_id = conv_resp.json()["id"]

        # 缺少 content 字段
        response = await async_client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"role": "user"},
        )
        assert response.status_code == 422


@pytest.mark.integration
@pytest.mark.asyncio
class TestListMessages:
    """列表消息。"""

    async def test_list_messages_stable_order(self, async_client: AsyncClient) -> None:
        """消息按创建时间 + ID 稳定排序。"""
        proj_resp = await async_client.post(
            "/api/v1/projects",
            json={"title": "排序测试"},
        )
        project_id = proj_resp.json()["id"]
        conv_resp = await async_client.post(
            f"/api/v1/projects/{project_id}/conversations",
            json={},
        )
        conversation_id = conv_resp.json()["id"]

        for i in range(3):
            await async_client.post(
                f"/api/v1/conversations/{conversation_id}/messages",
                json={"role": "user", "content": f"消息 {i}"},
            )

        response = await async_client.get(
            f"/api/v1/conversations/{conversation_id}/messages"
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 3
        sequences = [m["sequence"] for m in data["items"]]
        assert sequences == [1, 2, 3]  # 严格升序

    async def test_list_messages_pagination(self, async_client: AsyncClient) -> None:
        """消息支持分页查询。"""
        proj_resp = await async_client.post(
            "/api/v1/projects",
            json={"title": "分页测试"},
        )
        project_id = proj_resp.json()["id"]
        conv_resp = await async_client.post(
            f"/api/v1/projects/{project_id}/conversations",
            json={},
        )
        conversation_id = conv_resp.json()["id"]

        for i in range(5):
            await async_client.post(
                f"/api/v1/conversations/{conversation_id}/messages",
                json={"role": "user", "content": f"消息 {i}"},
            )

        response = await async_client.get(
            f"/api/v1/conversations/{conversation_id}/messages?limit=2"
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 2
        assert data["total"] == 5
