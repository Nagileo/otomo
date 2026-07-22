FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ① 先装重依赖（CPU 版 torch + bge/RAG），这一层只随依赖变化——
#    改业务代码不会让它失效，重部署从 5 分钟降到几十秒（Docker 层缓存）。
RUN pip install --upgrade pip \
    && pip install torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install "sentence-transformers>=3.0" "rank-bm25>=0.2"

# ② 再装核心依赖 + 本地包（改代码只重跑这两步，都很快）
COPY pyproject.toml ./
COPY otomo ./otomo
RUN pip install -e ".[rag]"

EXPOSE 8000

CMD ["uvicorn", "otomo.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
