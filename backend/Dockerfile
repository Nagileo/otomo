FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
COPY otomo ./otomo

# 带 RAG/语义模型（CPU 版 torch，避免拉 2G+ 的 CUDA 版）：
#   语义推荐重排（bge-small）+ RAG hybrid 精排（bge-reranker）。
#   不需要模型的轻装部署可改回 `pip install -e .`（语义功能自动降级到词法）。
RUN pip install --upgrade pip \
    && pip install torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install -e ".[rag]"

EXPOSE 8000

CMD ["uvicorn", "otomo.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
