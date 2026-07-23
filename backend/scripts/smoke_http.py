"""HTTP smoke test for the local Otomo backend.

Default checks are cheap and should not call the LLM:

    python scripts/smoke_http.py --start-server

Optional checks:

    python scripts/smoke_http.py --dev-token-login --chat "你好，简单介绍一下你自己"

The script is intentionally separate from pytest because it can hit real
network services and a locally running FastAPI app.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


PNG_1X1 = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


class Smoke:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.csrf = ""

    def ok(self, name: str, **extra: Any) -> None:
        self.rows.append({"ok": True, "step": name, **extra})
        print(f"OK   {name}")

    def fail(self, name: str, error: str, **extra: Any) -> None:
        self.rows.append({"ok": False, "step": name, "error": error, **extra})
        print(f"FAIL {name}: {error}")

    def headers(self) -> dict[str, str]:
        return {"x-otomo-csrf": self.csrf} if self.csrf else {}


async def _wait_health(base_url: str, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            async with httpx.AsyncClient(base_url=base_url, timeout=2.5) as client:
                res = await client.get("/health")
                if res.status_code == 200:
                    return
                last = f"HTTP {res.status_code}"
        except Exception as exc:  # noqa: BLE001
            last = f"{type(exc).__name__}: {exc}"
        await asyncio.sleep(0.5)
    raise RuntimeError(f"backend did not become healthy: {last}")


def _start_server(port: int) -> subprocess.Popen:
    env = os.environ.copy()
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "otomo.api.app:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )


async def _read_chat_sse(client: httpx.AsyncClient, smoke: Smoke, message: str) -> None:
    events: list[str] = []
    answer = ""
    async with client.stream(
        "POST",
        "/chat",
        headers={**smoke.headers(), "Content-Type": "application/json"},
        json={"message": message, "session_id": "smoke-http", "runner": "adaptive"},
        timeout=90,
    ) as res:
        if res.status_code != 200:
            raise RuntimeError(f"chat returned HTTP {res.status_code}: {await res.aread()}")
        async for line in res.aiter_lines():
            if not line.startswith("data:"):
                continue
            payload = json.loads(line[5:].strip())
            events.append(payload.get("type", "unknown"))
            if payload.get("type") == "answer_delta":
                answer += payload.get("text") or ""
            elif payload.get("type") == "final":
                answer = payload.get("answer") or answer
    if "final" not in events:
        raise RuntimeError(f"chat stream did not emit final; events={events}")
    smoke.ok("chat_sse", events=len(events), answer_preview=answer[:80])


async def _run(args: argparse.Namespace) -> int:
    smoke = Smoke()
    proc: subprocess.Popen | None = None
    base_url = args.base_url.rstrip("/")
    if args.start_server:
        proc = _start_server(args.port)
        base_url = f"http://127.0.0.1:{args.port}"
    try:
        await _wait_health(base_url)
        async with httpx.AsyncClient(base_url=base_url, timeout=30, follow_redirects=False) as client:
            health = await client.get("/health")
            health.raise_for_status()
            smoke.ok("health", payload=health.json())

            session = await client.get("/auth/session")
            session.raise_for_status()
            session_payload = session.json()
            smoke.csrf = session_payload.get("csrf_token") or ""
            if not smoke.csrf:
                raise RuntimeError("/auth/session did not return csrf_token")
            smoke.ok(
                "auth_session",
                authenticated=session_payload.get("authenticated"),
                oauth_configured=session_payload.get("oauth_configured"),
                dev_token_available=session_payload.get("dev_token_available"),
            )

            upload = await client.post(
                "/uploads/image",
                headers=smoke.headers(),
                json={"data_url": PNG_1X1, "filename": "smoke.png"},
            )
            upload.raise_for_status()
            upload_payload = upload.json()
            smoke.ok("upload_image", uri=upload_payload.get("uri"), size=upload_payload.get("size"))

            preview = await client.get(upload_payload["preview_url"])
            preview.raise_for_status()
            smoke.ok("upload_preview", content_type=preview.headers.get("content-type"), bytes=len(preview.content))

            search = await client.post(
                "/feedback/visual/search_subjects",
                headers=smoke.headers(),
                json={"keyword": args.search_keyword, "subject_type": "anime", "limit": 3},
            )
            search.raise_for_status()
            search_payload = search.json()
            smoke.ok("visual_feedback_search", count=len(search_payload.get("subjects") or []))

            if args.dev_token_login:
                login = await client.post("/auth/dev-token-login", headers=smoke.headers(), json={})
                login.raise_for_status()
                login_payload = login.json()
                new_csrf = ((login_payload.get("identity") or {}).get("csrf_token") or "")
                if new_csrf:
                    smoke.csrf = new_csrf
                smoke.ok("dev_token_login", username=(login_payload.get("identity") or {}).get("username"))
                feedback = await client.post(
                    "/feedback/visual",
                    headers=smoke.headers(),
                    json={
                        "image_uri": upload_payload.get("uri") or "",
                        "predicted_title": "smoke",
                        "predicted_subject_id": 207195,
                        "predicted_subject_name": "摇曳露营△",
                        "signal": "ambiguous",
                        "note": "http smoke",
                    },
                )
                feedback.raise_for_status()
                smoke.ok("visual_feedback_record", feedback_id=(feedback.json().get("feedback") or {}).get("id"))

            if args.chat:
                await _read_chat_sse(client, smoke, args.chat)
    except Exception as exc:  # noqa: BLE001
        smoke.fail("smoke", f"{type(exc).__name__}: {exc}")
        print(json.dumps(smoke.rows, ensure_ascii=False, indent=2))
        return 1
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
    print(json.dumps(smoke.rows, ensure_ascii=False, indent=2))
    return 0 if all(row.get("ok") for row in smoke.rows) else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke-test the local Otomo HTTP API.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--start-server", action="store_true", help="start a temporary uvicorn backend")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--search-keyword", default="摇曳露营")
    parser.add_argument("--dev-token-login", action="store_true", help="also test local BANGUMI_TOKEN login and visual feedback write")
    parser.add_argument("--chat", default="", help="also test /chat SSE; this can call the configured LLM")
    raise SystemExit(asyncio.run(_run(parser.parse_args())))


if __name__ == "__main__":
    main()
