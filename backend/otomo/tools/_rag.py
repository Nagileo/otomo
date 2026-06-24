"""RAG 共享：纯文本切块 + 按查询的词法（bigram）排序。萌娘 / 维基等多源复用。"""
from __future__ import annotations


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
    qb = _bigrams(query)
    if not qb:
        return chunks[:top_k]
    scored = [(sum(1 for b in _bigrams(c) if b in qb), i, c) for i, c in enumerate(chunks)]
    scored.sort(key=lambda x: (-x[0], x[1]))
    top = [c for score, _i, c in scored[:top_k] if score > 0]
    if chunks and chunks[0] not in top:  # 导言通常含核心信息，带上
        top = [chunks[0]] + top
    return top[:top_k] if top else chunks[:1]
