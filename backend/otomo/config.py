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
    # 分级：默认用 provider(免费优先 tavily)；高质量需求升级到 quality_provider(serper便宜/bocha中文最佳)。
    # 各引擎 key 全配好，切只改这两行。tavily/exa 月1000免费；serper 2500一次后$1/千(便宜+中文好)；bocha 1000/3月后¥36/千(最佳但贵)
    websearch_provider: str = "tavily"          # 主引擎（免费优先）
    websearch_quality_provider: str = "serper"  # 高质量升级引擎
    websearch_api_key: str = ""                 # 通用兜底
    websearch_tavily_key: str = ""
    websearch_serper_key: str = ""
    websearch_exa_key: str = ""
    websearch_bocha_key: str = ""

    def websearch_key(self, provider: str | None = None) -> str:
        """取指定（或当前主）引擎的 key，未填则回退通用 key。"""
        p = provider or self.websearch_provider
        per = {
            "tavily": self.websearch_tavily_key,
            "serper": self.websearch_serper_key,
            "exa": self.websearch_exa_key,
            "bocha": self.websearch_bocha_key,
        }.get(p, "")
        return per or self.websearch_api_key

    # ---- Agent / HTTP ----
    agent_max_iters: int = 8
    http_timeout: float = 30.0
    cache_ttl: float = 300.0  # Bangumi 响应内存缓存秒数（A5 换 Redis）


settings = Settings()
