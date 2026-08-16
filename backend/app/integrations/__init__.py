"""DramaAgent 外部集成（I-04）。

目前包含 MCP（Model Context Protocol）工具适配器：
把外部 HTTP JSON-RPC 服务暴露的工具映射为内部 Tool 协议，
供确定性工具链路复用。无 MCP 配置（默认关）时主流程完全不受影响。
"""
