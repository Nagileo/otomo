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

    # ---- 萌娘百科 RAG（按需取+缓存，绝不入库；见 docs/02）----
    moegirl_api_base: str = "https://zh.moegirl.org.cn/api.php"
    moegirl_user_agent: str = "otomo-rag/0.1 (+https://github.com/otomo-dev/otomo; non-commercial research)"

    # ---- 中文维基 RAG（CC BY-SA，有全文搜索；补关系/剧情）----
    wiki_api_base: str = "https://zh.wikipedia.org/w/api.php"
    wiki_user_agent: str = "otomo-rag/0.1 (+https://github.com/otomo-dev/otomo)"

    # ---- LLM（OpenAI 兼容，默认 DeepSeek）----
    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: str = ""
    llm_model: str = "deepseek-v4-flash"

    # ---- Web search（全网兜底，provider 可换；不填 key 则 web_search 工具优雅报"未配置"）----
    # tavily/exa 每月 1000 次免费(个人开发首选)；serper 中文质量最佳但免费是一次性 2500；bocha 需预充值
    websearch_provider: str = "tavily"
    websearch_api_key: str = ""

    # ---- Agent / HTTP ----
    agent_max_iters: int = 8
    http_timeout: float = 30.0
    cache_ttl: float = 300.0  # Bangumi 响应内存缓存秒数（A5 换 Redis）


settings = Settings()
