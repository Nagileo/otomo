"""集中配置：从环境变量 / .env 读取（见 docs/08-llm-and-config）。"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # ---- Bangumi ----
    bangumi_api_base: str = "https://api.bgm.tv"
    # 强制 User-Agent，通用 UA 会被 Bangumi 拒绝
    bangumi_user_agent: str = "otomo-dev/otomo/0.1 (+https://github.com/yourname/otomo)"
    bangumi_token: str | None = None

    # ---- LLM（OpenAI 兼容，默认 DeepSeek）----
    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: str = ""
    llm_model: str = "deepseek-v4-flash"

    # ---- Agent / HTTP ----
    agent_max_iters: int = 8
    http_timeout: float = 30.0
    cache_ttl: float = 300.0  # Bangumi 响应内存缓存秒数（A5 换 Redis）


settings = Settings()
