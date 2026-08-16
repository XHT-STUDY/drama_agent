"""可观测性模块（I-02）。

- metrics.py：进程内 Counter / Gauge / Histogram 注册表 + Prometheus 文本渲染
- tracing.py：request_id → run_id → node 的 contextvar 关联
- diagnostics.py：按 run 聚合事件表，输出运行诊断

MVP 不引入外部监控依赖（无 OpenTelemetry / Prometheus client），
指标经 GET /metrics 以 Prometheus 文本格式暴露。
"""
