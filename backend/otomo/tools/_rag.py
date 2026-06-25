"""RAG 共享：切块 + 检索排序（萌娘 / 维基等多源复用）。

两档检索，按依赖自动选、缺失自动降级：
- **hybrid_rank**（第二刀）：BM25(词法) + bge dense(语义) RRF 融合召回 → bge-reranker 精排。
  需 sentence-transformers + rank-bm25；模型懒加载、单例常驻。
- **rank_chunks**（兜底）：bigram 词法，零依赖。缺模型 / 出错时 hybrid 自动回退到它。

合规：只对**临时取来的单页**在内存编码、**不持久化向量**，符合萌娘 ai-train=no / 不入库红线。
持久跨页索引（仅许可源）属层 2，另做。
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_RECALL = 10  # hybrid：RRF 融合后送进 reranker 的候选数
_RRF_K = 60   # RRF 常数（经验值）
# 本地模型目录（otomo/models/<名>）优先，找不到才走 HF Hub / 缓存。
# 适配 modelscope 离线下载：modelscope download --model BAAI/bge-reranker-v2-m3 --local_dir otomo/models/bge-reranker-v2-m3
_LOCAL_MODELS = Path(__file__).resolve().parents[3] / "models"


def _resolve_model(hf_name: str) -> str:
    local = _LOCAL_MODELS / hf_name.split("/")[-1]
    return str(local) if local.is_dir() else hf_name


def chunk_text(text: str, size: int = 400) -> list[str]:
    chunks: list[str] = []
    buf = ""
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if len(buf) + len(line) + 1 > size and buf:
            chunks.append(buf)
            buf = line
        else:
            buf = f"{buf}\n{line}" if buf else line
    if buf:
        chunks.append(buf)
    return chunks


def _bigrams(s: str) -> set[str]:
    s = "".join(ch for ch in s if ch.strip())
    return {s[i : i + 2] for i in range(len(s) - 1)} if len(s) >= 2 else {s}


def rank_chunks(query: str, chunks: list[str], top_k: int = 3) -> list[str]:
    """bigram 词法排序（零依赖兜底）。"""
    qb = _bigrams(query)
    if not qb:
        return chunks[:top_k]
    scored = [(sum(1 for b in _bigrams(c) if b in qb), i, c) for i, c in enumerate(chunks)]
    scored.sort(key=lambda x: (-x[0], x[1]))
    top = [c for score, _i, c in scored[:top_k] if score > 0]
    if chunks and chunks[0] not in top:  # 导言通常含核心信息，带上
        top = [chunks[0]] + top
    return top[:top_k] if top else chunks[:1]


# --------------------------------------------------------------------------- #
# 第二刀：hybrid（BM25 + bge dense）RRF 融合 → bge-reranker 精排
# --------------------------------------------------------------------------- #


@lru_cache(maxsize=1)
def _embedder():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(_resolve_model("BAAI/bge-small-zh-v1.5"))


@lru_cache(maxsize=1)
def _reranker():
    from sentence_transformers import CrossEncoder

    return CrossEncoder(_resolve_model("BAAI/bge-reranker-v2-m3"))


def _rrf_ranks(scores: list[float]) -> list[int]:
    """分数 → 名次（0=最高）。"""
    order = sorted(range(len(scores)), key=lambda i: -scores[i])
    rank = [0] * len(scores)
    for r, i in enumerate(order):
        rank[i] = r
    return rank


def hybrid_rank(query: str, chunks: list[str], top_k: int = 3) -> list[str]:
    """BM25 + bge dense 用 RRF 融合召回 → bge-reranker 精排。缺依赖 / 出错 → 词法降级。"""
    if len(chunks) <= top_k:
        return chunks
    try:
        from rank_bm25 import BM25Okapi

        tok = list  # 中文按字 token（简单稳健，无需分词器）
        bm_scores = list(BM25Okapi([tok(c) for c in chunks]).get_scores(tok(query)))

        emb = _embedder()
        cvecs = emb.encode(chunks, normalize_embeddings=True)
        qvec = emb.encode([query], normalize_embeddings=True)[0]
        dense = [float(v) for v in (cvecs @ qvec)]

        rb, rd = _rrf_ranks(bm_scores), _rrf_ranks(dense)
        rrf = [1.0 / (_RRF_K + rb[i]) + 1.0 / (_RRF_K + rd[i]) for i in range(len(chunks))]
        cand = sorted(range(len(chunks)), key=lambda i: -rrf[i])[:_RECALL]

        scores = _reranker().predict([(query, chunks[i]) for i in cand])
        ranked = [chunks[i] for i, _s in sorted(zip(cand, scores), key=lambda x: -float(x[1]))]
        return ranked[:top_k]
    except Exception:  # noqa: BLE001 — 缺模型 / 依赖 / 运行错 → 词法兜底，绝不让检索挂掉
        return rank_chunks(query, chunks, top_k)
