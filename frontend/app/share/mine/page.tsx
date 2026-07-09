"use client";

import { useEffect, useState } from "react";

const BACKEND = process.env.NEXT_PUBLIC_BACKEND ?? "http://localhost:8000";

type AnyRecord = Record<string, any>;
type Notice = { tone: "good" | "warn" | "bad"; text: string };

function list(value: any): AnyRecord[] {
  return Array.isArray(value) ? value : [];
}

export default function MyShareSnapshotsPage() {
  const [csrf, setCsrf] = useState("");
  const [snapshots, setSnapshots] = useState<AnyRecord[]>([]);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void bootstrap();
  }, []);

  async function bootstrap() {
    const res = await fetch(`${BACKEND}/auth/session`, { credentials: "include" });
    const auth = await res.json().catch(() => ({}));
    setCsrf(auth.csrf_token || "");
    await loadShares(auth.csrf_token || "");
  }

  async function loadShares(token = csrf) {
    setBusy(true);
    try {
      const res = await fetch(`${BACKEND}/share/mine`, {
        credentials: "include",
        headers: token ? { "x-otomo-csrf": token } : {},
      });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok || !payload.ok) {
        setNotice({ tone: "bad", text: payload.detail || payload.error || `读取失败：HTTP ${res.status}` });
        return;
      }
      setSnapshots(list(payload.snapshots));
      setNotice({ tone: "good", text: "分享列表已同步。" });
    } finally {
      setBusy(false);
    }
  }

  async function revoke(snapshot: AnyRecord) {
    setBusy(true);
    try {
      const res = await fetch(`${BACKEND}/share/snapshots/${snapshot.id}`, {
        method: "DELETE",
        credentials: "include",
        headers: csrf ? { "x-otomo-csrf": csrf } : {},
      });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok || !payload.ok) {
        setNotice({ tone: "bad", text: payload.detail || payload.error || `撤销失败：HTTP ${res.status}` });
        return;
      }
      setNotice({ tone: "good", text: "分享页已撤销。" });
      await loadShares();
    } finally {
      setBusy(false);
    }
  }

  async function copy(url: string) {
    await navigator.clipboard?.writeText(url).catch(() => undefined);
    setNotice({ tone: "good", text: `链接已复制：${url}` });
  }

  return (
    <main className="share-page">
      <header className="share-hero">
        <div>
          <div className="share-kicker">Otomo Share</div>
          <h1>我的分享页</h1>
          <p>管理由产品面板生成的公开脱敏快照。撤销后原 URL 会返回 404。</p>
          <div className="share-badges">
            <span>{snapshots.length} snapshots</span>
            <span>public unlisted</span>
          </div>
        </div>
      </header>
      {notice && <div className={`auth-notice ${notice.tone}`}>{notice.text}</div>}
      <section className="share-section">
        <div className="settings-actions">
          <button className="inline-action" onClick={() => loadShares()} disabled={busy}>刷新</button>
          <a className="inline-action" href="/">回到首页</a>
        </div>
        <div className="share-list">
          {snapshots.map((snapshot) => (
            <div className="rating-card" key={snapshot.id}>
              <div className="rating-source">{snapshot.type} · {snapshot.visibility} · {snapshot.created_at}</div>
              <div className="card-title">{snapshot.title}</div>
              <p className="card-note">
                剧透 {snapshot.spoiler_level} · {snapshot.personalized ? "脱敏个性化" : "泛化公开"}
                {snapshot.expires_at ? ` · expires ${snapshot.expires_at}` : ""}
              </p>
              <div className="settings-actions">
                <a className="inline-action primary" href={snapshot.url} target="_blank" rel="noreferrer">打开</a>
                <button className="inline-action" onClick={() => copy(snapshot.url)} disabled={busy}>复制</button>
                <button className="inline-action" onClick={() => revoke(snapshot)} disabled={busy || snapshot.visibility === "revoked"}>撤销</button>
              </div>
            </div>
          ))}
          {!snapshots.length && <p className="share-dim">还没有分享页。回到首页，在作品档案、补番路线、月报或季番导视面板中生成。</p>}
        </div>
      </section>
    </main>
  );
}
