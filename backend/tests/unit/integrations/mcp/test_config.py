"""MCP 配置解析与 URL 安全校验单元测试（MCP-01）。

不访问网络：DNS 解析类校验注入 resolver。
"""

from __future__ import annotations

import logging

import pytest

from app.core.config import Settings
from app.core.errors import MCPConfigInvalidError
from app.integrations.mcp.protocol import (
    MCPServerConfig,
    load_mcp_server_configs,
    parse_mcp_servers_json,
)
from app.integrations.mcp.security import (
    assert_resolved_hosts_public,
    validate_server_url,
)


def _settings(**kwargs: object) -> Settings:
    return Settings(app_env="test", **kwargs)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _reset_legacy_warning_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """每个测试重置模块级弃用警告标志，避免跨测试顺序耦合。"""
    import app.integrations.mcp.protocol as protocol

    monkeypatch.setattr(protocol, "_legacy_warned", False)


# ======================================================================
# MCP_SERVERS_JSON 解析
# ======================================================================


class TestParseServersJson:
    def test_valid_multi_server_config(self) -> None:
        configs = parse_mcp_servers_json(
            """
            [
              {
                "id": "research",
                "url": "https://mcp-research.example.com/mcp",
                "allowed_tools": ["search", "fetch"],
                "bearer_token_env": "MCP_RESEARCH_TOKEN",
                "timeout_seconds": 45,
                "max_concurrency": 2
              },
              {
                "id": "assets",
                "url": "https://mcp-assets.example.com/mcp"
              }
            ]
            """
        )
        assert [c.id for c in configs] == ["research", "assets"]
        assert configs[0].allowed_tools == ["search", "fetch"]
        assert configs[0].bearer_token_env == "MCP_RESEARCH_TOKEN"
        assert configs[0].timeout_seconds == 45
        assert configs[0].max_concurrency == 2
        # 默认值
        assert configs[1].enabled is True
        assert configs[1].allowed_tools == []
        assert configs[1].bearer_token_env is None
        assert configs[1].timeout_seconds == 30.0
        assert configs[1].max_concurrency == 4
        assert configs[1].allow_private_network is False

    def test_unknown_field_rejected(self) -> None:
        with pytest.raises(Exception, match="extra_forbidden|Extra inputs"):
            parse_mcp_servers_json('[{"id": "a", "url": "https://x.example.com", "oops": 1}]')

    def test_invalid_json_rejected(self) -> None:
        with pytest.raises(ValueError, match="MCP_SERVERS_JSON"):
            parse_mcp_servers_json("{not json")

    def test_non_array_rejected(self) -> None:
        with pytest.raises(ValueError, match="数组"):
            parse_mcp_servers_json('{"id": "a"}')

    def test_duplicate_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="重复"):
            parse_mcp_servers_json(
                '[{"id": "a", "url": "https://a.example.com"},'
                ' {"id": "a", "url": "https://b.example.com"}]'
            )

    @pytest.mark.parametrize("bad_id", ["Upper", "has space", "中文", "a-b", ""])
    def test_invalid_id_rejected(self, bad_id: str) -> None:
        with pytest.raises(ValueError):
            MCPServerConfig(id=bad_id, url="https://x.example.com")

    def test_safe_summary_has_no_url_path_or_token(self) -> None:
        config = MCPServerConfig(
            id="research",
            url="https://user:secret@mcp.example.com/path?token=x",
            bearer_token_env="MCP_TOKEN",
        )
        summary = config.safe_summary()
        assert summary["id"] == "research"
        assert "secret" not in str(summary)
        assert "token=x" not in str(summary)


class TestLoadServerConfigs:
    def test_disabled_returns_empty(self) -> None:
        settings = _settings(mcp_enabled=False, mcp_servers_json='[{"id":"a","url":"https://x"}]')
        assert load_mcp_server_configs(settings) == []

    def test_missing_everything_returns_empty(self) -> None:
        settings = _settings(mcp_enabled=True)
        assert load_mcp_server_configs(settings) == []

    def test_servers_json_takes_precedence(self) -> None:
        settings = _settings(
            mcp_enabled=True,
            mcp_servers_json='[{"id":"new","url":"https://new.example.com"}]',
            mcp_base_url="https://old.example.com",
        )
        configs = load_mcp_server_configs(settings)
        assert [c.id for c in configs] == ["new"]

    def test_legacy_base_url_mapped_to_default_server(self, caplog: pytest.LogCaptureFixture) -> None:
        settings = _settings(
            mcp_enabled=True,
            mcp_base_url="http://localhost:9000",
            mcp_timeout_seconds=12.5,
        )
        with caplog.at_level(logging.WARNING, logger="app.integrations.mcp.protocol"):
            configs = load_mcp_server_configs(settings)
        assert len(configs) == 1
        assert configs[0].id == "default"
        assert configs[0].url == "http://localhost:9000"
        assert configs[0].timeout_seconds == 12.5
        # 兼容映射记录一次弃用警告
        assert any("弃用" in r.message for r in caplog.records)

    def test_legacy_warning_logged_once(self, caplog: pytest.LogCaptureFixture) -> None:
        settings = _settings(mcp_enabled=True, mcp_base_url="http://localhost:9000")
        with caplog.at_level(logging.WARNING, logger="app.integrations.mcp.protocol"):
            load_mcp_server_configs(settings)
            load_mcp_server_configs(settings)
        warnings = [r for r in caplog.records if "弃用" in r.message]
        assert len(warnings) == 1


# ======================================================================
# URL 安全校验（SSRF 边界）
# ======================================================================


class TestValidateServerUrl:
    def test_https_public_accepted_in_production(self) -> None:
        url = validate_server_url(
            "https://mcp.example.com/mcp", allow_private_network=False, app_env="production"
        )
        assert url == "https://mcp.example.com/mcp"

    def test_http_rejected_in_production(self) -> None:
        with pytest.raises(MCPConfigInvalidError, match="HTTPS"):
            validate_server_url(
                "http://mcp.example.com/mcp", allow_private_network=False, app_env="production"
            )

    def test_http_allowed_in_dev(self) -> None:
        validate_server_url(
            "http://mcp.internal:9000/mcp", allow_private_network=True, app_env="local"
        )

    def test_bad_scheme_rejected(self) -> None:
        with pytest.raises(MCPConfigInvalidError):
            validate_server_url(
                "file:///etc/passwd", allow_private_network=True, app_env="local"
            )

    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1:9000/mcp",
            "http://10.0.0.5/mcp",
            "http://192.168.1.4/mcp",
            "http://172.16.0.1/mcp",
            "http://169.254.169.254/latest/meta-data",
            "http://[::1]:9000/mcp",
            "http://[fe80::1]/mcp",
            "http://localhost:9000/mcp",
        ],
    )
    def test_private_targets_rejected_without_flag(self, url: str) -> None:
        with pytest.raises(MCPConfigInvalidError):
            validate_server_url(url, allow_private_network=False, app_env="local")

    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1:9000/mcp",
            "http://10.0.0.5/mcp",
            "http://localhost:9000/mcp",
        ],
    )
    def test_private_targets_allowed_with_flag(self, url: str) -> None:
        validate_server_url(url, allow_private_network=True, app_env="local")

    def test_public_ip_accepted(self) -> None:
        validate_server_url("https://93.184.216.34/mcp", allow_private_network=False, app_env="production")


class TestResolvedHosts:
    def test_dns_resolved_private_rejected(self) -> None:
        with pytest.raises(MCPConfigInvalidError, match="内网地址"):
            assert_resolved_hosts_public(
                "https://internal.example.com/mcp",
                allow_private_network=False,
                resolver=lambda host: ["203.0.113.9", "10.1.2.3"],
            )

    def test_dns_resolved_public_accepted(self) -> None:
        assert_resolved_hosts_public(
            "https://public.example.com/mcp",
            allow_private_network=False,
            resolver=lambda host: ["93.184.216.34"],
        )

    def test_unresolvable_host_rejected(self) -> None:
        with pytest.raises(MCPConfigInvalidError, match="无法解析"):
            assert_resolved_hosts_public(
                "https://nx.example.com/mcp",
                allow_private_network=False,
                resolver=lambda host: [],
            )

    def test_private_flag_skips_resolution(self) -> None:
        assert_resolved_hosts_public(
            "https://internal.example.com/mcp",
            allow_private_network=True,
            resolver=lambda host: [],
        )
