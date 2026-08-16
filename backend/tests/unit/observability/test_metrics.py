"""observability/metrics 单元测试（I-02）。

覆盖 Counter / Gauge / Histogram 的累积语义与 Prometheus 文本渲染
（TYPE/HELP/bucket/sum/count、标签顺序、reset 隔离）。
不依赖 DB / Redis / LLM。
"""

from __future__ import annotations

import pytest

from app.observability.metrics import Counter, Gauge, Histogram, MetricRegistry


class TestCounter:
    def test_inc_and_get(self) -> None:
        c = Counter("t_total", "测试计数", ("action",))
        assert c.get(action="create_script") == 0.0
        c.inc(action="create_script")
        c.inc(action="create_script", value=2)
        assert c.get(action="create_script") == 3.0
        # 缺失标签 → 空串 key，与显式空串等价
        assert c.get() == 0.0

    def test_negative_value_clamped(self) -> None:
        c = Counter("t_total", "计数不可负")
        c.inc(value=-5)
        assert c.get() == 0.0

    def test_render_format(self) -> None:
        c = Counter("t_total", "测试计数", ("action", "status"))
        c.inc(action="create_script", status="queued")
        c.inc(action="create_script", status="completed")
        lines: list[str] = []
        c.render(lines)
        assert "# TYPE t_total counter" in lines
        assert "# HELP t_total 测试计数" in lines
        assert 't_total{action="create_script",status="queued"} 1' in lines
        assert 't_total{action="create_script",status="completed"} 1' in lines

    def test_render_sorted_and_no_labels(self) -> None:
        c = Counter("t2_total", "无标签计数")
        c.inc()
        lines: list[str] = []
        c.render(lines)
        assert "t2_total 1" in lines


class TestGauge:
    def test_inc_dec_get(self) -> None:
        g = Gauge("t_active", "活跃数")
        g.inc()
        g.inc()
        g.dec()
        assert g.get() == 1.0
        g.dec(value=2)
        assert g.get() == -1.0


class TestHistogram:
    def test_buckets_accumulate(self) -> None:
        h = Histogram("t_dur", "耗时", ("node",), buckets=(0.1, 0.5, float("inf")))
        h.observe(0.2, node="normalize")  # 落入 le=0.5
        h.observe(0.7, node="normalize")  # 落入 le=inf
        assert h.get_count(node="normalize") == 2
        assert h.get_sum(node="normalize") == pytest.approx(0.9)

    def test_render_histogram_format(self) -> None:
        h = Histogram("t_dur", "耗时", ("node",), buckets=(0.1, 0.5, float("inf")))
        h.observe(0.2, node="normalize")
        lines: list[str] = []
        h.render(lines)
        assert "# TYPE t_dur histogram" in lines
        assert 't_dur_bucket{node="normalize",le="0.1"} 0' in lines
        assert 't_dur_bucket{node="normalize",le="0.5"} 1' in lines
        assert 't_dur_bucket{node="normalize",le="inf"} 1' in lines
        assert 't_dur_sum{node="normalize"} 0.2' in lines
        assert 't_dur_count{node="normalize"} 1' in lines


class TestMetricRegistry:
    def test_dedupe_by_name(self) -> None:
        r = MetricRegistry()
        a = r.counter("x_total", "同一指标")
        b = r.counter("x_total", "不同 help 也复用")
        assert a is b

    def test_reset(self) -> None:
        r = MetricRegistry()
        c = r.counter("y_total", "计数")
        g = r.gauge("y_active", "仪表")
        h = r.histogram("y_dur", "耗时")
        c.inc(action="a", status="s")
        g.inc()
        h.observe(0.1)
        r.reset()
        assert c.get(action="a", status="s") == 0.0
        assert g.get() == 0.0
        assert h.get_count() == 0.0

    def test_render_aggregates_all_types(self) -> None:
        r = MetricRegistry()
        r.counter("z_total", "计数", ("kind",)).inc(kind="prompt")
        r.gauge("z_active", "仪表").inc()
        r.histogram("z_dur", "耗时").observe(0.01)
        out = r.render_prometheus()
        assert "# TYPE z_total counter" in out
        assert "# TYPE z_active gauge" in out
        assert "# TYPE z_dur histogram" in out
        assert 'z_total{kind="prompt"} 1' in out
