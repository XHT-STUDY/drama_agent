"""MCP Server URL 安全校验——HTTPS 强制、私网/回环目标拒绝（SSRF 边界）。

校验分两层（MCP_DESIGN §11.1）：
- ``validate_server_url``：语法层。生产只允许 HTTPS；loopback、link-local、
  私网、保留 IP 与 ``localhost`` 主机名只有 ``allow_private_network=true``
  才放行；
- ``assert_resolved_hosts_public``：解析层。DNS 解析后的全部地址都必须是
  公网地址（防域名解析到内网 IP 的 DNS rebinding / SSRF），在连接前调用。

跨源重定向由官方 SDK 的 Streamable HTTP 传输保证（默认不跟随重定向，
仅在端点同源内跟随）；本模块不重复实现。
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urlparse

from app.core.errors import MCPConfigInvalidError

# 生产环境只允许的 scheme
_PRODUCTION_SCHEMES = frozenset({"https"})
# 本地/测试环境额外允许 http（连接本地 in-process / 开发 Server）
_DEV_SCHEMES = frozenset({"https", "http"})


def _is_disallowed_address(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """判断 IP 是否落在拒绝目标：回环 / 链路本地 / 私网 / 保留 / 多播 / 未指定。"""
    return (
        ip.is_loopback
        or ip.is_link_local
        or ip.is_private
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def validate_server_url(
    url: str,
    *,
    allow_private_network: bool,
    app_env: str,
) -> str:
    """校验 Server URL 并返回原 URL。

    Args:
        url: Streamable HTTP 端点。
        allow_private_network: 显式允许 loopback / 私网目标。
        app_env: 运行环境（production 下强制 HTTPS）。

    Raises:
        MCPConfigInvalidError: scheme 非法、host 缺失或命中拒绝目标。
    """
    label = "MCP Server URL"
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        raise MCPConfigInvalidError(detail=f"{label} 非法: {exc}") from exc

    allowed_schemes = _PRODUCTION_SCHEMES if app_env == "production" else _DEV_SCHEMES
    if parsed.scheme not in allowed_schemes:
        raise MCPConfigInvalidError(
            detail=(
                f"{label} 只允许 HTTPS"
                if app_env == "production"
                else f"{label} 只允许 HTTP/HTTPS"
            )
        )

    host = parsed.hostname
    if not host:
        raise MCPConfigInvalidError(detail=f"{label} 缺少 host")

    if _host_is_disallowed(host) and not allow_private_network:
        raise MCPConfigInvalidError(
            detail=f"{label} 指向回环或私网地址，需要显式 allow_private_network=true"
        )
    return url


def _host_is_disallowed(host: str) -> bool:
    """host 是 IP 字面量或 localhost 类名称时判断是否命中拒绝目标。"""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host.lower() in {"localhost", "localhost.localdomain"}
    return _is_disallowed_address(ip)


def _default_resolver(host: str) -> list[str]:
    """默认 DNS 解析（host → IP 字符串列表；失败返回空列表）。"""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return []
    return [str(info[4][0]) for info in infos]


def assert_resolved_hosts_public(
    url: str,
    *,
    allow_private_network: bool,
    resolver: Callable[[str], list[str]] | None = None,
) -> None:
    """DNS 解析后的全部地址必须是公网地址（allow_private_network=true 时跳过）。

    Args:
        url: 待连接的 Server URL。
        resolver: 可注入的解析函数（host → IP 字符串列表），默认 socket.getaddrinfo。

    Raises:
        MCPConfigInvalidError: 域名解析失败或解析结果命中拒绝目标。
    """
    if allow_private_network:
        return
    host = urlparse(url).hostname
    if not host:
        return  # 语法层校验已拒绝，防御性跳过
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        # IP 字面量已由 validate_server_url 校验
        if _is_disallowed_address(ip):
            raise MCPConfigInvalidError(
                detail="MCP Server 地址指向回环或私网地址，需要显式 allow_private_network=true"
            )
        return

    resolved = (resolver or _default_resolver)(host)
    if not resolved:
        raise MCPConfigInvalidError(detail=f"MCP Server 域名无法解析: {host}")
    for raw_ip in resolved:
        try:
            addr = ipaddress.ip_address(raw_ip)
        except ValueError:
            continue  # 非 IP 形态（如本地别名），交给语法层结论
        if _is_disallowed_address(addr):
            raise MCPConfigInvalidError(
                detail=f"MCP Server 域名解析到内网地址（{host}），已拒绝"
            )
