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
    bangumi_user_agent: str = "Nagileo/otomo (+https://github.com/Nagileo/otomo)"
    bangumi_token: str | None = None
    bangumi_oauth_client_id: str = ""
    bangumi_oauth_client_secret: str = ""
    bangumi_oauth_redirect_uri: str = "http://localhost:8000/auth/bangumi/callback"
    frontend_base_url: str = "http://localhost:3000"
    cors_allowed_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    auth_store_backend: str = "sqlite"  # sqlite | file
    auth_store_path: str = "cache/auth/auth.sqlite3"
    auth_encryption_key: str = ""       # Fernet key；空则开发环境自动生成 cache/auth/.fernet_key
    session_cookie_name: str = "otomo_session"
    csrf_cookie_name: str = "otomo_csrf"
    csrf_header_name: str = "x-otomo-csrf"
    cookie_secure: bool = False          # 生产 HTTPS 必须设 true
    csrf_protection_enabled: bool = True
    session_ttl_seconds: int = 60 * 60 * 24 * 30

    # ---- 萌娘百科 RAG（按需取+缓存，绝不入库；见 docs/02）----
    moegirl_api_base: str = "https://zh.moegirl.org.cn/api.php"
    moegirl_user_agent: str = "Nagileo/otomo-rag (+https://github.com/Nagileo/otomo; non-commercial research)"

    # ---- 中文维基 RAG（CC BY-SA，有全文搜索；补关系/剧情）----
    wiki_api_base: str = "https://zh.wikipedia.org/w/api.php"
    wiki_user_agent: str = "Nagileo/otomo-rag (+https://github.com/Nagileo/otomo)"

    # ---- LLM（OpenAI 兼容，默认 DeepSeek）----
    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: str = ""
    llm_model: str = "deepseek-v4-flash"
    vlm_base_url: str = ""
    vlm_api_key: str = ""
    vlm_model: str = ""
    vlm_provider: str = ""       # 可填 aliyun-bailian / siliconflow / gemini 等，仅用于 trace/配置说明
    vlm_ocr_hint: str = ""       # 给 Qwen-VL/OCR 类模型的额外提示，不配置则用默认截图识别提示

    # ---- Web search（全网兜底，provider 可换；不填 key 则 web_search 工具优雅报"未配置"）----
    # 分级：默认用 provider(免费优先 tavily)；高质量需求升级到 quality_provider(serper便宜/bocha中文最佳)。
    # 各引擎 key 全配好，切只改这两行。tavily/exa 月1000免费；serper 2500一次后$1/千(便宜+中文好)；bocha 1000/3月后¥36/千(最佳但贵)
    websearch_provider: str = "tavily"          # 普通查询主引擎（免费优先）
    websearch_quality_provider: str = "bocha"   # 高质量首选（博查二创/中文话语最好）；失败/配额满自动降级
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

    # ---- 离线协同召回（CF）----
    # recsys-offline 训练导出的 item-item 相似度表，作在线 recommend 的"协同召回 provider"
    # （看过 X 的人也看 Y，补在线天生缺失的协同信号）。按 i2i_{subject_type}.json 加载；
    # 文件缺失则该路召回静默跳过（优雅降级，不影响标签/图谱召回）。
    cf_i2i_dir: str = "otomo/data"

    # ---- 可观测（可选 Langfuse；不配则只用本地 trace JSONL，见 obs.py）----
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"
    trajectory_capture_enabled: bool = True
    trajectory_store_observations: bool = True
    weekly_scheduler_enabled: bool = True
    weekly_scheduler_interval_seconds: int = 900

    # ---- Agent / HTTP ----
    agent_max_iters: int = 8
    http_timeout: float = 30.0
    cache_ttl: float = 300.0  # Bangumi 响应内存缓存秒数（A5 换 Redis）
    upload_max_image_bytes: int = 6 * 1024 * 1024
    browser_fetch_timeout_ms: int = 15000
    browser_fetch_max_scrolls: int = 3

    # ---- 多模态溯源（可选；不配置则仅返回 trace.moe / 导航链接）----
    saucenao_api_key: str = ""


settings = Settings()
