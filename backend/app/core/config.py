"""应用配置模块 — 使用 Pydantic Settings 管理环境变量。

所有环境变量从 .env 文件或系统环境读取。
test 环境自动强制 FakeLLM，不调用外部模型服务。
"""

import os
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_settings.sources import PydanticBaseSettingsSource

# .env 文件绝对路径（项目根目录）
_ENV_FILE = Path(__file__).resolve().parent.parent.parent.parent / ".env"


class Settings(BaseSettings):
    """DramaAgent 全局配置。

    所有字段均从环境变量自动读取（不区分大小写），
    缺失必需字段时 Pydantic 抛出 ValidationError 并指出变量名。
    """

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE) if _ENV_FILE.exists() else None,
        env_file_encoding="utf-8",
        extra="ignore",  # .env 含 docker-compose 共用字段，忽略未定义变量
    )

    # ---- 运行环境 ----
    app_env: Literal["local", "test", "production"] = "local"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # ---- 数据库 ----
    database_url: str = "postgresql+asyncpg://drama:drama@localhost:5432/drama"
    database_url_sync: str = "postgresql://drama:drama@localhost:5432/drama"
    database_echo: bool = False

    # ---- Redis ----
    redis_url: str = "redis://localhost:6379/0"

    # ---- 文件存储路径（相对于项目根目录） ----
    artifact_file_root: str = "./var/artifacts"
    upload_file_root: str = "./var/uploads"

    # ---- LLM 通用配置 ----
    llm_provider: str = "openai_compatible"
    llm_api_base: str = ""
    llm_api_key: str = ""
    llm_timeout_seconds: int = 180
    llm_max_retries: int = 2

    # ---- 各角色模型名 ----
    llm_normalizer_model: str = ""
    llm_planner_model: str = ""
    llm_writer_model: str = ""
    llm_evaluator_model: str = ""
    llm_reviser_model: str = ""
    llm_summarizer_model: str = ""

    # ---- Embedding ----
    embedding_provider: str = ""
    embedding_model: str = ""
    embedding_dimension: int = 0  # 空字符串在 .env 中会被忽略（extra="ignore"）

    # ---- MVP 业务参数 ----
    mvp_outline_count: int = 10
    mvp_script_count: int = 3
    auto_revision_threshold: int = 75
    max_revision_rounds: int = 1
    short_term_message_count: int = 12
    context_max_tokens: int = 24000
    rag_top_k: int = 5
    upload_max_bytes: int = 10_485_760  # 10 MB

    # ---- 记忆（G-01） ----
    short_term_ttl_seconds: int = 7 * 24 * 3600  # 短期记忆 Redis 缓存 TTL（秒，滑动窗口）
    conversation_summary_threshold: int = 24  # 会话消息数达到该值整数倍时生成摘要

    # ---- CORS ----
    # 逗号分隔的允许来源列表；在 .env 中写作 "CORS_ORIGINS=*" 或 "CORS_ORIGINS=host1,host2"
    cors_origins: str = "*"

    def get_cors_origins(self) -> list[str]:
        """将 cors_origins 字符串解析为列表。"""
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [h.strip() for h in self.cors_origins.split(",") if h.strip()]

    # ---- SSE ----
    sse_heartbeat_seconds: int = 15

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """条件性加载 .env 源：test 环境跳过，避免真实配置泄漏到测试中。"""
        if os.environ.get("APP_ENV") == "test":
            return (init_settings, env_settings, file_secret_settings)
        return (init_settings, env_settings, dotenv_settings, file_secret_settings)

    def apply_env_overrides(self) -> "Settings":
        """根据 APP_ENV 施加环境特定的强制覆盖。

        test 环境：
        - LLM_PROVIDER 强制设为 "fake"，避免测试意外调用外部模型
        - EMBEDDING_PROVIDER 强制设为 "fake"
        - 日志级别默认提升到 WARNING

        production 环境：
        - 日志级别默认 INFO，禁止 DEBUG
        """
        if self.app_env == "test":
            self.llm_provider = "fake"
            self.embedding_provider = "fake"
            if self.log_level == "DEBUG":
                self.log_level = "WARNING"
        elif self.app_env == "production":
            if self.log_level == "DEBUG":
                self.log_level = "INFO"
        return self

    def ensure_directories(self) -> list[Path]:
        """创建运行时需要的本地目录（var/artifacts、var/uploads）。

        目录内容不提交 Git（由 .gitignore 覆盖）。
        返回已创建（或已存在）的目录路径列表。
        """
        created: list[Path] = []
        for root_str in (self.artifact_file_root, self.upload_file_root):
            path = Path(root_str).resolve()
            path.mkdir(parents=True, exist_ok=True)
            created.append(path)
        return created


def load_settings(*, env: str | None = None) -> Settings:
    """加载应用配置的工厂函数。

    Args:
        env: 可选的环境覆盖。为 None 时从 APP_ENV 环境变量读取。

    Returns:
        已施加环境覆盖的 Settings 实例。

    若在测试中调用，传入 env="test" 可确保 FakeLLM 行为，
    比依赖环境变量更可靠。
    """
    if env is not None:
        os.environ["APP_ENV"] = env

    settings = Settings()
    settings.apply_env_overrides()

    # local 环境自动创建运行时目录
    if settings.app_env == "local":
        settings.ensure_directories()

    return settings
