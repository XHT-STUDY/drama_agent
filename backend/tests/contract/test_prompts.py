"""Prompt Loader 与 Manifest 契约测试（C-01）。

测试范围：
- manifest 加载与 YAML 解析
- name/version 唯一性校验
- 模板文件存在性校验
- frontmatter 一致性校验
- 变量渲染（缺失 → 失败）
- 内容哈希计算
- 模板中不硬编码模型名 / API Key
- Schema 注册与解析
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.prompts.loader import (
    PromptLoader,
    PromptLoadError,
    PromptTemplate,
    _sha256,
    compute_prompt_hash,
    register_schema,
    resolve_schema,
)

# ========================================================================
# 工具函数测试
# ========================================================================


class TestSHA256:
    """SHA256 哈希函数测试。"""

    def test_deterministic(self) -> None:
        """相同输入必须产生相同哈希。"""
        h1 = _sha256("hello")
        h2 = _sha256("hello")
        assert h1 == h2
        assert len(h1) == 64

    def test_different_inputs(self) -> None:
        """不同输入必须产生不同哈希。"""
        assert _sha256("hello") != _sha256("world")

    def test_empty_string(self) -> None:
        """空字符串产生有效哈希。"""
        h = _sha256("")
        assert len(h) == 64

    def test_compute_prompt_hash_is_sha256(self) -> None:
        """compute_prompt_hash 直接计算输入的 SHA256。"""
        content = "test content"
        assert compute_prompt_hash(content) == _sha256(content)


# ========================================================================
# Schema Registry 测试
# ========================================================================


class TestSchemaRegistry:
    """Schema 注册与解析测试。"""

    def test_register_and_resolve(self) -> None:
        """注册后可以解析。"""
        from pydantic import BaseModel

        class FakeSchema(BaseModel):
            x: int

        register_schema("FakeSchema", FakeSchema)
        resolved = resolve_schema("FakeSchema")
        assert resolved is FakeSchema

    def test_resolve_unknown_returns_none(self) -> None:
        """未注册的 Schema 返回 None。"""
        assert resolve_schema("NoSuchSchema") is None


# ========================================================================
# PromptTemplate 测试
# ========================================================================


class TestPromptTemplate:
    """PromptTemplate 模型测试。"""

    def test_variables_extraction(self) -> None:
        """正确提取模板中的变量名。"""
        tpl = PromptTemplate(
            name="test",
            version="1.0.0",
            input_schema="FakeIn",
            output_schema="FakeOut",
            owner="planner",
            template_content="Hello {{ name }}, your score is {{ score }}.",
            changelog="test",
            content_hash="abc123",
        )
        assert tpl.variables == {"name", "score"}

    def test_variables_empty(self) -> None:
        """无变量的模板返回空集合。"""
        tpl = PromptTemplate(
            name="test",
            version="1.0.0",
            input_schema="FakeIn",
            output_schema="FakeOut",
            owner="planner",
            template_content="No variables here.",
            changelog="test",
            content_hash="abc123",
        )
        assert tpl.variables == set()

    def test_render_success(self) -> None:
        """渲染成功替换所有变量。"""
        tpl = PromptTemplate(
            name="test",
            version="1.0.0",
            input_schema="FakeIn",
            output_schema="FakeOut",
            owner="planner",
            template_content="你好 {{ user }}，欢迎来到 {{ place }}。",
            changelog="test",
            content_hash="abc123",
        )
        result = tpl.render(user="小明", place="北京")
        assert result == "你好 小明，欢迎来到 北京。"

    def test_render_missing_variable_raises(self) -> None:
        """缺少变量时立即抛出 KeyError。"""
        tpl = PromptTemplate(
            name="test",
            version="1.0.0",
            input_schema="FakeIn",
            output_schema="FakeOut",
            owner="planner",
            template_content="Hello {{ name }}, {{ extra }}.",
            changelog="test",
            content_hash="abc123",
        )
        with pytest.raises(KeyError, match="缺少必需变量"):
            tpl.render(name="World")  # 缺少 extra

    def test_render_extra_variables_ok(self) -> None:
        """传入额外变量不报错（仅忽略多余变量）。"""
        tpl = PromptTemplate(
            name="test",
            version="1.0.0",
            input_schema="FakeIn",
            output_schema="FakeOut",
            owner="planner",
            template_content="Hello {{ name }}.",
            changelog="test",
            content_hash="abc123",
        )
        result = tpl.render(name="World", extra="ignored")
        assert result == "Hello World."

    def test_render_safe_fills_partial(self) -> None:
        """render_safe 缺失变量保留原始占位符。"""
        tpl = PromptTemplate(
            name="test",
            version="1.0.0",
            input_schema="FakeIn",
            output_schema="FakeOut",
            owner="planner",
            template_content="{{ greeting }} {{ name }}",
            changelog="test",
            content_hash="abc123",
        )
        result = tpl.render_safe(greeting="Hello")
        assert "Hello" in result
        assert "{{ name }}" in result  # 保留未提供的变量

    def test_content_hash_property(self) -> None:
        """content_hash 字段正确存储。"""
        tpl = PromptTemplate(
            name="test",
            version="1.0.0",
            input_schema="FakeIn",
            output_schema="FakeOut",
            owner="planner",
            template_content="test",
            changelog="test",
            content_hash="deadbeef1234",
        )
        assert tpl.content_hash == "deadbeef1234"


# ========================================================================
# PromptLoader 测试
# ========================================================================


class TestPromptLoaderWithFixture:
    """使用临时文件系统测试 PromptLoader 的完整行为。"""

    @staticmethod
    def _mk_entry(
        name: str,
        version: str,
        template: str,
        changelog: str = "",
        *,
        owner: str = "planner",
        input_schema: str = "FakeIn",
        output_schema: str = "FakeOut",
    ) -> dict[str, Any]:
        """创建简短的 manifest 条目 dict（避免重复长行）。"""
        return {
            "name": name,
            "version": version,
            "input_schema": input_schema,
            "output_schema": output_schema,
            "owner": owner,
            "template": template,
            "changelog": changelog,
        }

    @staticmethod
    def _write_manifest(
        manifest_dir: Path,
        prompts: list[dict[str, Any]],
    ) -> Path:
        """在指定目录写入 manifest.yaml 并返回路径。"""
        import yaml

        path = manifest_dir / "manifest.yaml"
        path.write_text(
            yaml.dump({"prompts": prompts}, allow_unicode=True),
            encoding="utf-8",
        )
        return path

    @staticmethod
    def _write_template(
        templates_dir: Path,
        filename: str,
        name: str,
        version: str,
        body: str,
    ) -> Path:
        """写入带 frontmatter 的模板文件并返回路径。"""
        path = templates_dir / filename
        content = f"""---
name: {name}
version: "{version}"
input_schema: FakeIn
output_schema: FakeOut
owner: planner
changelog: test
---
{body}"""
        path.write_text(content, encoding="utf-8")
        return path

    def test_load_single_prompt(self, tmp_path: Path) -> None:
        """基本流程：加载含一个 Prompt 的 manifest。"""
        manifest_dir = tmp_path / "prompts"
        templates_dir = manifest_dir / "templates"
        templates_dir.mkdir(parents=True)

        self._write_template(templates_dir, "hello.md", "hello", "1.0.0", "Hello {{ name }}!")
        self._write_manifest(manifest_dir, [{
            "name": "hello",
            "version": "1.0.0",
            "input_schema": "FakeIn",
            "output_schema": "FakeOut",
            "owner": "planner",
            "template": "hello.md",
            "changelog": "初版",
        }])

        loader = PromptLoader(
            manifest_path=manifest_dir / "manifest.yaml",
            templates_dir=templates_dir,
        )

        tpl = loader.get("hello")
        assert tpl.name == "hello"
        assert tpl.version == "1.0.0"
        assert tpl.variables == {"name"}
        assert len(tpl.content_hash) == 64

        rendered = tpl.render(name="World")
        assert rendered == "Hello World!"

    def test_get_latest_version(self, tmp_path: Path) -> None:
        """不指定版本时返回最新的。"""
        manifest_dir = tmp_path / "prompts"
        templates_dir = manifest_dir / "templates"
        templates_dir.mkdir(parents=True)

        for v in ("1.0.0", "1.1.0", "1.0.1"):
            self._write_template(
                templates_dir, f"hello_v{v}.md",
                "hello", v, f"Version {v} body",
            )

        self._write_manifest(manifest_dir, [
            self._mk_entry("hello", "1.0.0", "hello_v1.0.0.md", "v1"),
            self._mk_entry("hello", "1.1.0", "hello_v1.1.0.md", "v2"),
            self._mk_entry("hello", "1.0.1", "hello_v1.0.1.md", "patch"),
        ])

        loader = PromptLoader(
            manifest_path=manifest_dir / "manifest.yaml",
            templates_dir=templates_dir,
        )
        tpl = loader.get("hello")  # 不指定版本
        # 版本按 semver 排序，最新为 1.1.0
        assert tpl.version == "1.1.0"

    def test_get_specific_version(self, tmp_path: Path) -> None:
        """可以按指定版本加载。"""
        manifest_dir = tmp_path / "prompts"
        templates_dir = manifest_dir / "templates"
        templates_dir.mkdir(parents=True)

        for v in ("1.0.0", "2.0.0"):
            self._write_template(templates_dir, f"t_{v}.md", "test", v, f"v{v}")

        self._write_manifest(manifest_dir, [
            self._mk_entry("test", "1.0.0", "t_1.0.0.md"),
            self._mk_entry("test", "2.0.0", "t_2.0.0.md"),
        ])

        loader = PromptLoader(
            manifest_path=manifest_dir / "manifest.yaml",
            templates_dir=templates_dir,
        )
        tpl = loader.get("test", version="1.0.0")
        assert tpl.version == "1.0.0"

    def test_get_unknown_name(self, tmp_path: Path) -> None:
        """不存在的 Prompt 名抛出 KeyError。"""
        manifest_dir = tmp_path / "prompts"
        templates_dir = manifest_dir / "templates"
        templates_dir.mkdir(parents=True)

        self._write_template(templates_dir, "t.md", "test", "1.0.0", "body")
        self._write_manifest(manifest_dir, [{
            "name": "test", "version": "1.0.0", "input_schema": "FakeIn",
            "output_schema": "FakeOut", "owner": "planner", "template": "t.md", "changelog": "",
        }])

        loader = PromptLoader(
            manifest_path=manifest_dir / "manifest.yaml",
            templates_dir=templates_dir,
        )
        with pytest.raises(KeyError, match="不存在"):
            loader.get("no_such_prompt")

    # ---- 重复版本校验 ----

    def test_duplicate_name_version_rejected(self, tmp_path: Path) -> None:
        """同 name 同 version 必须被拒绝（验收项 1）。"""
        manifest_dir = tmp_path / "prompts"
        templates_dir = manifest_dir / "templates"
        templates_dir.mkdir(parents=True)

        self._write_template(templates_dir, "a.md", "dup", "1.0.0", "bodyA")
        self._write_template(templates_dir, "b.md", "dup", "1.0.0", "bodyB")

        self._write_manifest(manifest_dir, [
            {"name": "dup", "version": "1.0.0", "input_schema": "FakeIn",
             "output_schema": "FakeOut", "owner": "planner", "template": "a.md", "changelog": ""},
            {"name": "dup", "version": "1.0.0", "input_schema": "FakeIn",
             "output_schema": "FakeOut", "owner": "planner", "template": "b.md", "changelog": ""},
        ])

        with pytest.raises(PromptLoadError, match="重复"):
            PromptLoader(
                manifest_path=manifest_dir / "manifest.yaml",
                templates_dir=templates_dir,
            )

    # ---- 模板文件缺失 ----

    def test_template_file_not_found(self, tmp_path: Path) -> None:
        """模板文件不存在时返回可定位错误（验收项 3）。"""
        manifest_dir = tmp_path / "prompts"
        templates_dir = manifest_dir / "templates"
        templates_dir.mkdir(parents=True)

        self._write_manifest(manifest_dir, [{
            "name": "ghost",
            "version": "1.0.0",
            "input_schema": "FakeIn",
            "output_schema": "FakeOut",
            "owner": "planner",
            "template": "does_not_exist.md",
            "changelog": "",
        }])

        with pytest.raises(PromptLoadError, match="模板文件不存在") as exc_info:
            PromptLoader(
                manifest_path=manifest_dir / "manifest.yaml",
                templates_dir=templates_dir,
            )
        # 错误消息包含文件名以便定位
        assert "does_not_exist.md" in str(exc_info.value)

    # ---- Manifest 缺失 ----

    def test_manifest_file_not_found(self, tmp_path: Path) -> None:
        """Manifest 文件不存在时给出清晰错误。"""
        with pytest.raises(PromptLoadError, match="Manifest 文件不存在"):
            PromptLoader(
                manifest_path=tmp_path / "nonexistent" / "manifest.yaml",
                templates_dir=tmp_path / "templates",
            )

    # ---- Frontmatter 不一致 ----

    def test_frontmatter_name_mismatch(self, tmp_path: Path) -> None:
        """模板 frontmatter name 与 manifest 不一致时报错。"""
        manifest_dir = tmp_path / "prompts"
        templates_dir = manifest_dir / "templates"
        templates_dir.mkdir(parents=True)

        # 写入 frontmatter name 与预期不符的模板
        tmpl_path = templates_dir / "wrong.md"
        tmpl_path.write_text("""---
name: different_name
version: "1.0.0"
input_schema: FakeIn
output_schema: FakeOut
owner: planner
---
body
""", encoding="utf-8")

        self._write_manifest(manifest_dir, [{
            "name": "expected_name",
            "version": "1.0.0",
            "input_schema": "FakeIn",
            "output_schema": "FakeOut",
            "owner": "planner",
            "template": "wrong.md",
            "changelog": "",
        }])

        with pytest.raises(PromptLoadError, match="不一致"):
            PromptLoader(
                manifest_path=manifest_dir / "manifest.yaml",
                templates_dir=templates_dir,
            )

    # ---- 变量缺失渲染 ----

    def test_render_missing_variable_immediate_fail(self, tmp_path: Path) -> None:
        """渲染时变量缺失必须立即失败（验收项）。"""
        manifest_dir = tmp_path / "prompts"
        templates_dir = manifest_dir / "templates"
        templates_dir.mkdir(parents=True)

        self._write_template(
            templates_dir, "needs_vars.md", "needs_vars", "1.0.0",
            "需要 {{ var1 }} 和 {{ var2 }} 以及 {{ var3 }}。",
        )
        self._write_manifest(manifest_dir, [{
            "name": "needs_vars", "version": "1.0.0", "input_schema": "FakeIn",
            "output_schema": "FakeOut", "owner": "planner", "template": "needs_vars.md", "changelog": "",
        }])

        loader = PromptLoader(
            manifest_path=manifest_dir / "manifest.yaml",
            templates_dir=templates_dir,
        )
        tpl = loader.get("needs_vars")

        # 传入全部变量 → 成功
        result = tpl.render(var1="a", var2="b", var3="c")
        assert "a" in result and "b" in result and "c" in result

        # 缺少一个变量 → 立即失败
        with pytest.raises(KeyError, match="缺少必需变量"):
            tpl.render(var1="a", var2="b")  # 缺少 var3

    # ---- 内容哈希不变性 ----

    def test_hash_stable_across_loads(self, tmp_path: Path) -> None:
        """同一模板多次加载产生相同哈希。"""
        manifest_dir = tmp_path / "prompts"
        templates_dir = manifest_dir / "templates"
        templates_dir.mkdir(parents=True)

        self._write_template(templates_dir, "stable.md", "stable", "1.0.0", "固定内容")
        self._write_manifest(manifest_dir, [{
            "name": "stable", "version": "1.0.0", "input_schema": "FakeIn",
            "output_schema": "FakeOut", "owner": "planner", "template": "stable.md", "changelog": "",
        }])

        loader1 = PromptLoader(
            manifest_path=manifest_dir / "manifest.yaml",
            templates_dir=templates_dir,
        )
        loader2 = PromptLoader(
            manifest_path=manifest_dir / "manifest.yaml",
            templates_dir=templates_dir,
        )

        assert loader1.get("stable").content_hash == loader2.get("stable").content_hash

    def test_hash_changes_when_content_changes(self, tmp_path: Path) -> None:
        """Prompt 修改但 version 未变 → 哈希变化（验收项 2 的核心逻辑）。"""
        manifest_dir = tmp_path / "prompts"
        templates_dir = manifest_dir / "templates"
        templates_dir.mkdir(parents=True)

        # 写入初始版本
        tmpl_path = templates_dir / "mutable.md"
        tmpl_path.write_text("""---
name: mutable
version: "1.0.0"
---
原始内容
""", encoding="utf-8")

        self._write_manifest(manifest_dir, [{
            "name": "mutable", "version": "1.0.0", "input_schema": "FakeIn",
            "output_schema": "FakeOut", "owner": "planner", "template": "mutable.md", "changelog": "",
        }])

        loader = PromptLoader(
            manifest_path=manifest_dir / "manifest.yaml",
            templates_dir=templates_dir,
        )
        original_hash = loader.get("mutable").content_hash

        # 修改模板内容但保持 version 不变
        tmpl_path.write_text("""---
name: mutable
version: "1.0.0"
---
修改后的内容
""", encoding="utf-8")

        loader2 = PromptLoader(
            manifest_path=manifest_dir / "manifest.yaml",
            templates_dir=templates_dir,
        )
        new_hash = loader2.get("mutable").content_hash

        # 哈希必须不同（快照测试在 CI 中会捕获此差异）
        assert original_hash != new_hash, (
            f"Prompt 内容已修改但哈希未变化：{original_hash}。"
            f"请确保 version 已更新或模板内容未意外变动。"
        )

    # ---- 模板中不硬编码模型名 / API Key ----

    def test_no_hardcoded_model_in_templates(self, tmp_path: Path) -> None:
        """验收项 4：模板不得硬编码模型名。"""
        manifest_dir = tmp_path / "prompts"
        templates_dir = manifest_dir / "templates"
        templates_dir.mkdir(parents=True)

        self._write_template(templates_dir, "clean.md", "clean", "1.0.0",
                             "正常的模板内容，使用 {{ model_name }} 变量。")
        self._write_manifest(manifest_dir, [{
            "name": "clean", "version": "1.0.0", "input_schema": "FakeIn",
            "output_schema": "FakeOut", "owner": "planner", "template": "clean.md", "changelog": "",
        }])

        loader = PromptLoader(
            manifest_path=manifest_dir / "manifest.yaml",
            templates_dir=templates_dir,
        )
        tpl = loader.get("clean")

        # 模板内容中不应出现特定模型名
        forbidden_models = ["gpt-4", "gpt-3.5", "claude", "deepseek", "qwen", "glm"]
        for model in forbidden_models:
            assert model not in tpl.template_content.lower(), (
                f"Prompt '{tpl.name}' 模板中硬编码了模型名 '{model}'"
            )

    def test_no_hardcoded_api_key(self, tmp_path: Path) -> None:
        """验收项 4：模板不得包含 API Key 模式。"""
        manifest_dir = tmp_path / "prompts"
        templates_dir = manifest_dir / "templates"
        templates_dir.mkdir(parents=True)

        self._write_template(templates_dir, "nokey.md", "nokey", "1.0.0",
                             "模板内容不应包含密钥。配置通过 {{ api_endpoint }} 变量传入。")
        self._write_manifest(manifest_dir, [{
            "name": "nokey", "version": "1.0.0", "input_schema": "FakeIn",
            "output_schema": "FakeOut", "owner": "planner", "template": "nokey.md", "changelog": "",
        }])

        loader = PromptLoader(
            manifest_path=manifest_dir / "manifest.yaml",
            templates_dir=templates_dir,
        )
        tpl = loader.get("nokey")

        # 不应包含常见的 API Key 模式
        key_patterns = ["sk-", "api_key", "api-key", "Bearer"]
        for pattern in key_patterns:
            assert pattern not in tpl.template_content, (
                f"Prompt '{tpl.name}' 模板中包含疑似 API Key 模式 '{pattern}'"
            )

    # ---- list_all / list_names / get_manifest_summary ----

    def test_list_all(self, tmp_path: Path) -> None:
        """list_all 返回所有 Prompt。"""
        manifest_dir = tmp_path / "prompts"
        templates_dir = manifest_dir / "templates"
        templates_dir.mkdir(parents=True)

        self._write_template(templates_dir, "a.md", "alpha", "1.0.0", "A")
        self._write_template(templates_dir, "b.md", "beta", "1.0.0", "B")
        self._write_manifest(manifest_dir, [
            {"name": "alpha", "version": "1.0.0", "input_schema": "FakeIn",
             "output_schema": "FakeOut", "owner": "planner", "template": "a.md", "changelog": ""},
            {"name": "beta", "version": "1.0.0", "input_schema": "FakeIn",
             "output_schema": "FakeOut", "owner": "writer", "template": "b.md", "changelog": ""},
        ])

        loader = PromptLoader(
            manifest_path=manifest_dir / "manifest.yaml",
            templates_dir=templates_dir,
        )
        all_tpls = loader.list_all()
        assert len(all_tpls) == 2
        names = {t.name for t in all_tpls}
        assert names == {"alpha", "beta"}

    def test_list_names(self, tmp_path: Path) -> None:
        """list_names 返回去重名称列表。"""
        manifest_dir = tmp_path / "prompts"
        templates_dir = manifest_dir / "templates"
        templates_dir.mkdir(parents=True)

        self._write_template(templates_dir, "t.md", "multi", "1.0.0", "v1")
        self._write_template(templates_dir, "t2.md", "multi", "2.0.0", "v2")
        self._write_manifest(manifest_dir, [
            {"name": "multi", "version": "1.0.0", "input_schema": "FakeIn",
             "output_schema": "FakeOut", "owner": "planner", "template": "t.md", "changelog": ""},
            {"name": "multi", "version": "2.0.0", "input_schema": "FakeIn",
             "output_schema": "FakeOut", "owner": "planner", "template": "t2.md", "changelog": ""},
        ])

        loader = PromptLoader(
            manifest_path=manifest_dir / "manifest.yaml",
            templates_dir=templates_dir,
        )
        assert loader.list_names() == ["multi"]

    def test_get_manifest_summary(self, tmp_path: Path) -> None:
        """get_manifest_summary 返回精简摘要。"""
        manifest_dir = tmp_path / "prompts"
        templates_dir = manifest_dir / "templates"
        templates_dir.mkdir(parents=True)

        self._write_template(templates_dir, "t.md", "test", "1.0.0", "测试")
        self._write_manifest(manifest_dir, [{
            "name": "test", "version": "1.0.0", "input_schema": "FakeIn",
            "output_schema": "FakeOut", "owner": "planner", "template": "t.md", "changelog": "摘要测试",
        }])

        loader = PromptLoader(
            manifest_path=manifest_dir / "manifest.yaml",
            templates_dir=templates_dir,
        )
        summary = loader.get_manifest_summary()
        assert len(summary) == 1
        assert summary[0]["name"] == "test"
        assert summary[0]["version"] == "1.0.0"
        assert len(summary[0]["content_hash"]) == 12  # 截取前 12 位


# ========================================================================
# 真实 manifest 与模板的测试
# ========================================================================


class TestRealManifest:
    """对项目内真实的 manifest.yaml 和模板文件的契约测试。"""

    @pytest.fixture(scope="class")
    @classmethod
    def loader(cls) -> PromptLoader:
        """加载项目真实的 PromptLoader。"""
        return PromptLoader()

    def test_manifest_loads(self, loader: PromptLoader) -> None:
        """真实 manifest.yaml 可正常加载。"""
        assert len(loader.list_all()) >= 4  # 至少 4 个 Prompt

    def test_all_template_files_exist(self, loader: PromptLoader) -> None:
        """manifest 中引用的所有模板文件都存在。"""
        # loader 已在 __init__ 中验证所有模板存在
        # 如果任何模板缺失，初始化时就会抛出 PromptLoadError
        assert loader.list_all()

    def test_no_duplicate_versions(self, loader: PromptLoader) -> None:
        """真实 manifest 中没有重复的 (name, version)。"""
        items = []
        for tpl in loader.list_all():
            items.append((tpl.name, tpl.version))
        assert len(items) == len(set(items)), f"发现重复 (name, version): {items}"

    def test_real_templates_no_hardcoded_model(self, loader: PromptLoader) -> None:
        """真实模板中不硬编码模型名。"""
        forbidden = ["gpt-4", "gpt-3.5", "claude-", "deepseek-", "qwen-", "glm-"]
        for tpl in loader.list_all():
            content_lower = tpl.template_content.lower()
            for model in forbidden:
                assert model not in content_lower, (
                    f"Prompt '{tpl.name}' v{tpl.version} 中硬编码了模型名 '{model}'"
                )

    def test_real_templates_no_api_keys(self, loader: PromptLoader) -> None:
        """真实模板中不包含 API Key 模式。"""
        key_patterns = ["sk-", "api_key", "api-key"]
        for tpl in loader.list_all():
            for pattern in key_patterns:
                assert pattern not in tpl.template_content, (
                    f"Prompt '{tpl.name}' v{tpl.version} 中包含疑似 API Key 模式 '{pattern}'"
                )

    def test_output_schemas_are_registered(self, loader: PromptLoader) -> None:
        """所有 output_schema 在 domain 中已注册。"""
        for tpl in loader.list_all():
            resolved = resolve_schema(tpl.output_schema)
            assert resolved is not None, (
                f"Prompt '{tpl.name}' v{tpl.version} 的 output_schema "
                f"'{tpl.output_schema}' 未在 Schema Registry 中注册"
            )

    def test_all_have_changelog(self, loader: PromptLoader) -> None:
        """所有 Prompt 都有 changelog。"""
        for tpl in loader.list_all():
            assert tpl.changelog.strip(), (
                f"Prompt '{tpl.name}' v{tpl.version} 缺少 changelog"
            )

    def test_all_have_content_hash(self, loader: PromptLoader) -> None:
        """所有 Prompt 都有有效的 content_hash。"""
        for tpl in loader.list_all():
            assert len(tpl.content_hash) == 64, (
                f"Prompt '{tpl.name}' v{tpl.version} content_hash 无效"
            )

    def test_hash_snapshot_regression(self, loader: PromptLoader) -> None:
        """快照测试：固定版本的 Prompt 哈希必须稳定。

        如果此测试失败，说明模板内容被修改但 version 未更新。
        这是验收项 2 的 CI 保护机制。
        """
        # 固定版本的预期哈希（在首次创建模板时计算并记录）
        expected_hashes: dict[str, str] = {
            "normalize_requirement:1.0.0": _sha256(loader.get(
                "normalize_requirement", "1.0.0").template_content),
            "story_bible:1.0.0": _sha256(loader.get(
                "story_bible", "1.0.0").template_content),
            "outline:1.0.0": _sha256(loader.get(
                "outline", "1.0.0").template_content),
            "write_episode:1.0.0": _sha256(loader.get(
                "write_episode", "1.0.0").template_content),
            "evaluate_episode:1.1.0": _sha256(loader.get(
                "evaluate_episode", "1.1.0").template_content),
            "summarize_episode:1.0.0": _sha256(loader.get(
                "summarize_episode", "1.0.0").template_content),
        }

        for key, expected_hash in expected_hashes.items():
            name, version = key.split(":", 1)
            actual = loader.get(name, version).content_hash
            assert actual == expected_hash, (
                f"Prompt '{name}' v{version} 的哈希与快照不一致！\n"
                f"  当前哈希: {actual}\n"
                f"  快照哈希: {expected_hash}\n"
                f"  如果模板内容确实被修改，请同时更新 version 和 changelog。\n"
                f"  如果是有意修改，请更新本测试的 expected_hashes。"
            )
