FROM python:3.12-slim AS core

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    OTOMO_ALLOW_MODEL_DOWNLOAD=0

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ① 先装重依赖（CPU 版 torch + bge/RAG），这一层只随依赖变化——
#    改业务代码不会让它失效，重部署从 5 分钟降到几十秒（Docker 层缓存）。
#    模型文件通过 /models 卷显式提供；镜像不在首个线上请求里偷偷下载数 GB 文件。
RUN pip install --upgrade pip \
    && pip install torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install "sentence-transformers>=3.0" "rank-bm25>=0.2"

# ② 再装核心依赖 + 本地包（改代码只重跑这两步，都很快）
COPY pyproject.toml ./
COPY otomo ./otomo
RUN pip install -e ".[rag,discord,obs]" \
    && rm -rf /var/lib/apt/lists/*

EXPOSE 8000

CMD ["uvicorn", "otomo.api.app:app", "--host", "0.0.0.0", "--port", "8000"]

# Optional capabilities are separate immutable targets. Normal production
# pulls only `core`; Playwright and Whisper never inflate that default image.
FROM core AS browser
RUN pip install -e ".[browser]" \
    && playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

FROM core AS asr
RUN pip install -e ".[asr]"
CMD ["uvicorn", "otomo.asr_worker:app", "--host", "0.0.0.0", "--port", "8010"]

FROM browser AS full
RUN pip install -e ".[asr,pixiv]"
