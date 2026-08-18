"""采集 Bangumi 用户公开收藏 → user-item 交互表，供**原生协同过滤(CF)**。

为何要采：离线(MAL anime_id)与在线(Bangumi subject_id)主键不通，离线 CF 产出无法反哺在线；
  且 Bangumi API 无跨用户共现。要让离线真正反哺在线，必须自建 Bangumi subject_id 维度的
  user-item 矩阵 → 训 i2i → 作为在线 recommend 的"协同召回 provider"（真闭环）。

怎么采：v0 无"用户发现 / 某条目收藏者"端点 → 用数字 UID 区间批量拉
  GET /v0/users/{uid}/collections（公开收藏免 token）。礼貌限流 + 并发上限 + 断点续传。

合规：仅个人研究、非商业；只取公开收藏；原始 user-item 数据本地训练用、不发布、已 gitignore；
  对外/上线只发布聚合的 item-item 相似度表（不含用户隐私）。

落地：data/bangumi/collections_{stype}.csv   表头 user_id,subject_id,ctype,rate,updated_at
续传：data/bangumi/_done_{stype}.txt          每行一个已采 uid（重跑自动跳过）

    python -m recsys_offline.bangumi_collect --start 1 --end 50000 --stype anime
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import os
import sys
import time
from typing import Literal

import httpx

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass

SUBJECT_TYPE = {"book": 1, "anime": 2, "music": 3, "game": 4, "real": 6}
# 礼貌 UA（Bangumi 拒绝通用 UA）。自建客户端，不接 bgm-cli/Bangumi-MCP。
USER_AGENT = (
    "otomo-recsys/0.1 (https://github.com/Nagileo/otomo; "
    "personal research, non-commercial)"
)
_BASE = "https://api.bgm.tv"
_RETRY_STATUS = {429, 500, 502, 503, 504}
_PAGE = 50
_SCHEMA_VERSION = 2
FetchState = Literal["ok", "missing", "failed"]


async def _fetch_page(
    client: httpx.AsyncClient, uid: int, stype: int, offset: int, retries: int = 3
) -> tuple[list[dict], FetchState]:
    """拉一页收藏；瞬时失败不得伪装成空收藏写入断点。"""
    params = {"subject_type": stype, "limit": _PAGE, "offset": offset}
    for attempt in range(retries):
        try:
            r = await client.get(f"/v0/users/{uid}/collections", params=params)
            if r.status_code == 404:
                return [], "missing"  # uid 不存在
            if r.status_code in _RETRY_STATUS:
                await asyncio.sleep(0.6 * (attempt + 1))
                continue
            r.raise_for_status()
            return (r.json().get("data") or []), "ok"
        except httpx.HTTPStatusError as e:
            if e.response is not None and e.response.status_code == 404:
                return [], "missing"
            await asyncio.sleep(0.6 * (attempt + 1))
        except httpx.TransportError:
            await asyncio.sleep(0.6 * (attempt + 1))
    return [], "failed"


async def fetch_user(
    client: httpx.AsyncClient, uid: int, stype: int, max_items: int = 400
) -> list[tuple[int, int, int, int, str]] | None:
    """某 uid 的公开收藏；失败返回 None，确保下次会重试。"""
    rows: list[tuple[int, int, int, int, str]] = []
    offset = 0
    while offset < max_items:
        data, state = await _fetch_page(client, uid, stype, offset)
        if state == "missing":
            return []
        if state == "failed":
            return None
        for it in data:
            subj = it.get("subject") or {}
            sid = subj.get("id")
            if sid:
                rows.append((
                    uid, int(sid), int(it.get("type") or 0), int(it.get("rate") or 0),
                    str(it.get("updated_at") or ""),
                ))
        if len(data) < _PAGE:
            break
        offset += _PAGE
        await asyncio.sleep(0.05)
    return rows


def _load_done(path: str) -> set[int]:
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8") as f:
        return {int(line) for line in f if line.strip().isdigit()}


def _ensure_timestamp_column(path: str) -> None:
    """One-time in-place schema migration for pre-temporal four-column CSVs."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8", newline="") as source:
        reader = csv.reader(source)
        header = next(reader, [])
        if "updated_at" in header:
            return
    temp = path + ".migrating"
    with open(path, encoding="utf-8", newline="") as source, open(
        temp, "w", encoding="utf-8", newline="",
    ) as target:
        reader = csv.reader(source)
        header = next(reader, [])
        writer = csv.writer(target)
        writer.writerow([*header, "updated_at"])
        for row in reader:
            writer.writerow([*row, ""])
    os.replace(temp, path)


async def collect(start: int, end: int, stype: int, outdir: str, concurrency: int, delay: float) -> None:
    os.makedirs(outdir, exist_ok=True)
    stype_name = next(k for k, v in SUBJECT_TYPE.items() if v == stype)
    csv_path = os.path.join(outdir, f"collections_{stype_name}.csv")
    # v2 re-fetches users once so pre-temporal rows receive real updated_at.
    done_path = os.path.join(outdir, f"_done_v{_SCHEMA_VERSION}_{stype_name}.txt")

    done = _load_done(done_path)
    todo = [u for u in range(start, end) if u not in done]
    print(f"采集 uid [{start},{end})  stype={stype_name}  待采 {len(todo)}（跳过已采 {len(done)}）")

    _ensure_timestamp_column(csv_path)
    new_csv = not os.path.exists(csv_path)
    csv_f = open(csv_path, "a", newline="", encoding="utf-8")
    done_f = open(done_path, "a", encoding="utf-8")
    writer = csv.writer(csv_f)
    if new_csv:
        writer.writerow(["user_id", "subject_id", "ctype", "rate", "updated_at"])

    sem = asyncio.Semaphore(concurrency)
    hit = inter = failed = 0
    t0 = time.monotonic()

    async with httpx.AsyncClient(
        base_url=_BASE, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, timeout=20.0
    ) as client:

        async def work(
            uid: int,
        ) -> tuple[int, list[tuple[int, int, int, int, str]] | None]:
            async with sem:
                rows = await fetch_user(client, uid, stype)
                await asyncio.sleep(delay)  # 礼貌限流
                return uid, rows

        try:
            for i in range(0, len(todo), 200):  # 分批 flush，便于中断续传
                batch = todo[i : i + 200]
                for coro in asyncio.as_completed([work(u) for u in batch]):
                    uid, rows = await coro
                    if rows is None:
                        failed += 1
                        continue
                    done_f.write(f"{uid}\n")
                    if rows:
                        hit += 1
                        inter += len(rows)
                        writer.writerows(rows)
                csv_f.flush()
                done_f.flush()
                el = time.monotonic() - t0
                seen_n = min(i + 200, len(todo))
                rate = seen_n / el if el else 0
                eta = (len(todo) - seen_n) / rate / 60 if rate else 0
                print(
                    f"  进度 {seen_n}/{len(todo)}  命中用户 {hit}  失败待重试 {failed}  "
                    f"交互 {inter}  "
                    f"{rate:.0f} uid/s  ETA {eta:.0f}min",
                    flush=True,
                )
        except KeyboardInterrupt:
            print("\n中断，已 flush，可重跑续传。")
        finally:
            csv_f.close()
            done_f.close()
    el = time.monotonic() - t0
    print(
        f"\n完成：命中用户 {hit}（命中率 {hit/max(len(todo),1)*100:.1f}%），"
        f"失败待重试 {failed}，交互 {inter}，耗时 {el/60:.1f}min"
    )
    print(f"  → {csv_path}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--end", type=int, default=50000, help="uid 区间右开")
    ap.add_argument("--stype", choices=list(SUBJECT_TYPE), default="anime")
    ap.add_argument("--outdir", default="data/bangumi")
    ap.add_argument("--concurrency", type=int, default=6, help="并发上限（礼貌，勿过高）")
    ap.add_argument("--delay", type=float, default=0.15, help="每请求后延迟秒（礼貌限流）")
    args = ap.parse_args()
    asyncio.run(collect(args.start, args.end, SUBJECT_TYPE[args.stype], args.outdir, args.concurrency, args.delay))


if __name__ == "__main__":
    main()
