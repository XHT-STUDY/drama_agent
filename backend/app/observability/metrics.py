"""进程内指标注册表与 Prometheus 文本渲染（I-02）。

MVP 不引入外部监控依赖：用进程内 Counter / Gauge / Histogram 累积
运行时指标，经 GET /metrics 以 Prometheus 文本格式暴露，供
Prometheus / Grafana 抓取。指标标签一律使用低基数值
（action / status / artifact_type / model / format / node 等），
禁止 project_id / run_id 等高基数值入标签（否则指标爆炸）。

埋点约定（§10.4）：
- workflow_runs_total{action,status}        Run 创建与状态变更
- workflow_node_duration_seconds{node}      创作节点耗时（秒）
- llm_calls_total{node,model,status}        LLM 调用结果
- llm_retry_total{reason}                   重试次数
- llm_token_usage_total{kind}               prompt / completion 用量
- artifact_created_total{artifact_type}     新建 Artifact 数
- export_total{format,status}               导出结果
- rag_retrieval_duration_seconds            知识库检索耗时（秒）
- sse_connections_active                    当前 SSE 连接数（gauge）
"""

from __future__ import annotations

import threading
from typing import Any

# Prometheus 直方图默认桶（秒），覆盖 API / 节点 / LLM 常见耗时区间
_DEFAULT_BUCKETS: tuple[float, ...] = (
    0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, float("inf"),
)


def _label_key(label_names: tuple[str, ...], labels: dict[str, Any]) -> tuple[str, ...]:
    """按声明顺序生成标签元组；缺失标签补空串，保证顺序稳定可测。"""
    return tuple(str(labels.get(name, "")) for name in label_names)


def _format_labels(label_names: tuple[str, ...], key: tuple[str, ...]) -> str:
    """把标签元组渲染为 Prometheus 标签段；无标签时返回空串。"""
    if not label_names:
        return ""
    inner = ",".join(f'{n}="{v}"' for n, v in zip(label_names, key, strict=True))
    return "{" + inner + "}"


class Counter:
    """单调递增计数器（workflow_runs_total 等）。"""

    def __init__(self, name: str, help_text: str, label_names: tuple[str, ...] = ()) -> None:
        self.name = name
        self.help = help_text
        self.label_names = label_names
        self._values: dict[tuple[str, ...], float] = {}
        self._lock = threading.Lock()

    def inc(self, value: float = 1.0, **labels: Any) -> None:
        """累加计数。value 必须 >= 0（Prometheus 计数语义）。"""
        key = _label_key(self.label_names, labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + max(0.0, value)

    def get(self, **labels: Any) -> float:
        """读取指定标签组合的当前值（测试/自检用）。"""
        return self._values.get(_label_key(self.label_names, labels), 0.0)

    def reset(self) -> None:
        with self._lock:
            self._values.clear()

    def render(self, lines: list[str]) -> None:
        lines.append(f"# TYPE {self.name} counter")
        lines.append(f"# HELP {self.name} {self.help}")
        with self._lock:
            for key, value in sorted(self._values.items()):
                lines.append(f"{self.name}{_format_labels(self.label_names, key)} {value:g}")


class Gauge:
    """可增可减的仪表（sse_connections_active）。"""

    def __init__(self, name: str, help_text: str, label_names: tuple[str, ...] = ()) -> None:
        self.name = name
        self.help = help_text
        self.label_names = label_names
        self._values: dict[tuple[str, ...], float] = {}
        self._lock = threading.Lock()

    def inc(self, value: float = 1.0, **labels: Any) -> None:
        self._add(value, labels)

    def dec(self, value: float = 1.0, **labels: Any) -> None:
        self._add(-value, labels)

    def _add(self, value: float, labels: dict[str, Any]) -> None:
        key = _label_key(self.label_names, labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + value

    def get(self, **labels: Any) -> float:
        return self._values.get(_label_key(self.label_names, labels), 0.0)

    def reset(self) -> None:
        with self._lock:
            self._values.clear()

    def render(self, lines: list[str]) -> None:
        lines.append(f"# TYPE {self.name} gauge")
        lines.append(f"# HELP {self.name} {self.help}")
        with self._lock:
            for key, value in sorted(self._values.items()):
                lines.append(f"{self.name}{_format_labels(self.label_names, key)} {value:g}")


class Histogram:
    """直方图（workflow_node_duration_seconds 等）。

    按 pre-defined 桶累计观测；渲染为 `name_bucket{le=..}` +
    `name_sum` + `name_count`，兼容 Prometheus 直方图语义。
    """

    def __init__(
        self,
        name: str,
        help_text: str,
        label_names: tuple[str, ...] = (),
        buckets: tuple[float, ...] = _DEFAULT_BUCKETS,
    ) -> None:
        self.name = name
        self.help = help_text
        self.label_names = label_names
        self.buckets = tuple(sorted({float(b) for b in buckets}))
        self._counts: dict[tuple[str, ...], float] = {}
        self._sums: dict[tuple[str, ...], float] = {}
        self._buckets: dict[tuple[str, ...], dict[float, float]] = {}
        self._lock = threading.Lock()

    def observe(self, value: float, **labels: Any) -> None:
        key = _label_key(self.label_names, labels)
        with self._lock:
            self._counts[key] = self._counts.get(key, 0.0) + 1.0
            self._sums[key] = self._sums.get(key, 0.0) + float(value)
            bc = self._buckets.get(key)
            if bc is None:
                bc = dict.fromkeys(self.buckets, 0.0)
                self._buckets[key] = bc
            for b in self.buckets:
                if float(value) <= b:
                    bc[b] = bc[b] + 1.0

    def get_count(self, **labels: Any) -> float:
        return self._counts.get(_label_key(self.label_names, labels), 0.0)

    def get_sum(self, **labels: Any) -> float:
        return self._sums.get(_label_key(self.label_names, labels), 0.0)

    def reset(self) -> None:
        with self._lock:
            self._counts.clear()
            self._sums.clear()
            self._buckets.clear()

    def render(self, lines: list[str]) -> None:
        lines.append(f"# TYPE {self.name} histogram")
        lines.append(f"# HELP {self.name} {self.help}")
        with self._lock:
            for key in sorted(self._buckets):
                labels = _format_labels(self.label_names, key)
                bc = self._buckets[key]
                # 标签内层（去外层花括号）；le 置于最末，符合 Prometheus 惯例
                label_inner = labels[1:-1] if labels else ""
                for b in self.buckets:
                    le_part = f'{label_inner},le="{b:g}"' if label_inner else f'le="{b:g}"'
                    lines.append(f"{self.name}_bucket{{{le_part}}} {bc[b]:g}")
                lines.append(f"{self.name}_sum{labels} {self._sums.get(key, 0.0):g}")
                lines.append(f"{self.name}_count{labels} {self._counts.get(key, 0.0):g}")


class MetricRegistry:
    """指标注册表：按名去重持有全部 Counter / Gauge / Histogram。"""

    def __init__(self) -> None:
        self._counters: dict[str, Counter] = {}
        self._gauges: dict[str, Gauge] = {}
        self._histograms: dict[str, Histogram] = {}
        self._lock = threading.Lock()

    def counter(self, name: str, help_text: str = "", label_names: tuple[str, ...] = ()) -> Counter:
        with self._lock:
            if name not in self._counters:
                self._counters[name] = Counter(name, help_text, label_names)
            return self._counters[name]

    def gauge(self, name: str, help_text: str = "", label_names: tuple[str, ...] = ()) -> Gauge:
        with self._lock:
            if name not in self._gauges:
                self._gauges[name] = Gauge(name, help_text, label_names)
            return self._gauges[name]

    def histogram(
        self,
        name: str,
        help_text: str = "",
        label_names: tuple[str, ...] = (),
        buckets: tuple[float, ...] = _DEFAULT_BUCKETS,
    ) -> Histogram:
        with self._lock:
            if name not in self._histograms:
                self._histograms[name] = Histogram(name, help_text, label_names, buckets)
            return self._histograms[name]

    def reset(self) -> None:
        """清空全部指标（测试隔离用）。"""
        with self._lock:
            for c in self._counters.values():
                c.reset()
            for g in self._gauges.values():
                g.reset()
            for h in self._histograms.values():
                h.reset()

    def render_prometheus(self) -> str:
        """渲染完整 Prometheus 文本格式输出。"""
        lines: list[str] = []
        with self._lock:
            for c in sorted(self._counters.values(), key=lambda m: m.name):
                c.render(lines)
            for g in sorted(self._gauges.values(), key=lambda m: m.name):
                g.render(lines)
            for h in sorted(self._histograms.values(), key=lambda m: m.name):
                h.render(lines)
        return ("\n".join(lines) + "\n") if lines else ""


# ---- 模块级单例注册表与命名指标 ----

registry = MetricRegistry()

workflow_runs_total = registry.counter(
    "workflow_runs_total",
    "Workflow Run 创建与状态变更总数",
    ("action", "status"),
)
workflow_node_duration_seconds = registry.histogram(
    "workflow_node_duration_seconds",
    "创作工作流节点执行耗时（秒）",
    ("node",),
)
llm_calls_total = registry.counter(
    "llm_calls_total",
    "LLM 调用结果（按节点/模型/状态分类；status=ok 或错误码）",
    ("node", "model", "status"),
)
llm_retry_total = registry.counter(
    "llm_retry_total",
    "可重试 LLM 错误触发的重试次数（按原因）",
    ("reason",),
)
llm_token_usage_total = registry.counter(
    "llm_token_usage_total",
    "LLM Token 用量（kind=prompt/completion）",
    ("kind",),
)
artifact_created_total = registry.counter(
    "artifact_created_total",
    "新建 Artifact 总数（按类型）",
    ("artifact_type",),
)
export_total = registry.counter(
    "export_total",
    "导出结果（按格式/成败分类）",
    ("format", "status"),
)
sse_connections_active = registry.gauge(
    "sse_connections_active",
    "当前活跃 SSE 连接数",
)
rag_retrieval_duration_seconds = registry.histogram(
    "rag_retrieval_duration_seconds",
    "知识库检索耗时（秒）",
)
