"use client";

import { useEffect, useMemo, useState } from "react";

const BACKEND = process.env.NEXT_PUBLIC_BACKEND ?? "http://localhost:8000";

type Item = {
  id: number; name: string; name_cn?: string; image?: string; url?: string;
  weekday_cn?: string; broadcast?: string; score?: number; collection_label?: string;
  my_ep?: number; aired_ep?: number; behind?: number; next_air_date?: string;
  next_episode?: number; hidden_this_season?: boolean; pinned?: boolean; action?: string;
};
type Day = { weekday_id: number; weekday_cn: string; items: Item[] };
type Cockpit = {
  date: string; today: Item[]; yesterday: Item[]; week: Day[]; hidden: Item[];
  backlog: Item[]; counts: Record<string, number>; notes: string[];
};
type Tab = "today" | "yesterday" | "week" | "backlog" | "hidden";

export default function TodayPage() {
  const [csrf, setCsrf] = useState("");
  const [data, setData] = useState<Cockpit | null>(null);
  const [tab, setTab] = useState<Tab>("today");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [pending, setPending] = useState<any>(null);

  useEffect(() => { void bootstrap(); }, []);

  async function bootstrap() {
    setBusy(true);
    const auth = await fetch(`${BACKEND}/auth/session`, { credentials: "include" }).then((r) => r.json()).catch(() => ({}));
    setCsrf(auth.csrf_token || "");
    if (!auth.authenticated) {
      setNotice("请先回到对话页绑定 Bangumi 账号。今日页只显示你的收藏与放送交集。");
      setBusy(false);
      return;
    }
    await load();
  }

  async function load() {
    setBusy(true);
    const response = await fetch(`${BACKEND}/today`, { credentials: "include" });
    const payload = await response.json().catch(() => ({}));
    if (response.ok && payload.ok) setData(payload.data);
    else setNotice(payload.detail || "今日追番数据加载失败");
    setBusy(false);
  }

  async function preference(item: Item, patch: Record<string, boolean>) {
    const response = await fetch(`${BACKEND}/today/preferences/${item.id}`, {
      method: "PATCH", credentials: "include",
      headers: { "Content-Type": "application/json", ...(csrf ? { "x-otomo-csrf": csrf } : {}) },
      body: JSON.stringify(patch),
    });
    if (response.ok) await load();
    else setNotice((await response.json().catch(() => ({}))).detail || "偏好更新失败");
  }

  async function prepareProgress(item: Item) {
    const upTo = Math.max(Number(item.my_ep || 0) + 1, 1);
    const response = await fetch(`${BACKEND}/actions/prepare-write`, {
      method: "POST", credentials: "include",
      headers: { "Content-Type": "application/json", ...(csrf ? { "x-otomo-csrf": csrf } : {}) },
      body: JSON.stringify({
        operation: "mark_episodes_watched", subject_id: item.id,
        subject_name: item.name_cn || item.name, up_to_episode: upTo,
        collection_type: 3, reason: `今日页标记看到第 ${upTo} 集`,
      }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || !payload.ok) setNotice(payload.detail || payload.error || "准备写回失败");
    else setPending(payload.data?.action || null);
  }

  async function resolveAction(confirm: boolean) {
    if (!pending?.id) return;
    setBusy(true);
    const response = await fetch(`${BACKEND}/actions/${confirm ? "confirm" : "cancel"}`, {
      method: "POST", credentials: "include",
      headers: { "Content-Type": "application/json", ...(csrf ? { "x-otomo-csrf": csrf } : {}) },
      body: JSON.stringify({ action_id: pending.id, reason: confirm ? "" : "用户取消" }),
    });
    const payload = await response.json().catch(() => ({}));
    setPending(null);
    setNotice(payload.data?.message || payload.error || (confirm ? "进度已写回" : "已取消"));
    if (confirm && response.ok) await load();
    setBusy(false);
  }

  const rows = useMemo(() => {
    if (!data) return [];
    if (tab === "week") return [];
    return data[tab] || [];
  }, [data, tab]);

  return (
    <main className="today-shell">
      <header className="today-header">
        <div>
          <a className="today-brand" href="/">Otomo · 今日追番</a>
          <div className="sub">你的收藏 × 今日放送 × 追番进度</div>
        </div>
        <div className="today-nav"><a className="ghost" href="/">返回对话</a><a className="ghost" href="/settings/subscriptions">提醒设置</a></div>
      </header>

      <nav className="today-tabs" aria-label="今日追番视图">
        {([
          ["today", "今天"], ["yesterday", "昨天"], ["week", "本周"],
          ["backlog", "落后"], ["hidden", "已隐藏"],
        ] as [Tab, string][]).map(([key, label]) => (
          <button key={key} className={tab === key ? "active" : ""} onClick={() => setTab(key)}>
            {label}{data ? ` ${key === "week" ? data.counts.week || 0 : data.counts[key] || 0}` : ""}
          </button>
        ))}
      </nav>

      {notice && <div className="today-notice">{notice}</div>}
      {busy && !data ? <div className="today-empty">正在核对放送表和追番进度…</div> : null}

      {tab === "week" && data ? (
        <div className="today-week">
          {data.week.map((day) => (
            <section className="today-day" key={day.weekday_id}>
              <h2>{day.weekday_cn}</h2>
              {day.items.length ? day.items.map((item) => <TodayRow key={item.id} item={item} onPreference={preference} onProgress={prepareProgress} />) : <div className="today-empty compact">没有你的在看/想看条目</div>}
            </section>
          ))}
        </div>
      ) : (
        <div className="today-list">
          {rows.map((item) => <TodayRow key={item.id} item={item} onPreference={preference} onProgress={prepareProgress} hiddenView={tab === "hidden"} />)}
          {data && !rows.length ? <div className="today-empty">这个视图暂无条目。</div> : null}
        </div>
      )}

      {data?.notes?.length ? <details className="today-notes"><summary>数据说明</summary>{data.notes.map((note) => <p key={note}>{note}</p>)}</details> : null}

      {pending ? (
        <div className="confirm-backdrop" role="dialog" aria-modal="true" aria-label="确认写回 Bangumi">
          <div className="confirm-dialog">
            <h2>确认写回 Bangumi</h2>
            <p>{pending.summary}</p>
            <div className="today-nav">
              <button className="ghost" onClick={() => void resolveAction(false)} disabled={busy}>取消</button>
              <button onClick={() => void resolveAction(true)} disabled={busy}>确认写回</button>
            </div>
          </div>
        </div>
      ) : null}
    </main>
  );
}

function TodayRow({ item, onPreference, onProgress, hiddenView = false }: {
  item: Item;
  onPreference: (item: Item, patch: Record<string, boolean>) => Promise<void>;
  onProgress: (item: Item) => Promise<void>;
  hiddenView?: boolean;
}) {
  const title = item.name_cn || item.name;
  const canAdvance = !item.aired_ep || Number(item.my_ep || 0) < Number(item.aired_ep);
  return (
    <article className={`today-item ${item.pinned ? "pinned" : ""}`}>
      <a href={item.url || `https://bgm.tv/subject/${item.id}`} target="_blank" rel="noreferrer">
        {item.image ? <img src={item.image} alt="" /> : <div className="today-cover" />}
      </a>
      <div className="today-main">
        <div className="today-item-head">
          <a href={item.url || `https://bgm.tv/subject/${item.id}`} target="_blank" rel="noreferrer">{title}</a>
          {item.pinned ? <span className="badge good">置顶</span> : null}
        </div>
        <div className="card-meta">
          {item.weekday_cn || "档期未定"}{item.broadcast ? ` · ${item.broadcast}` : ""}
          {item.score ? ` · BGM ${item.score}` : ""}
        </div>
        <div className="today-progress">
          <span>{item.collection_label || "收藏"}</span>
          <span>看到 {item.my_ep || 0}{item.aired_ep ? ` / 已播 ${item.aired_ep}` : ""}</span>
          {Number(item.behind || 0) > 0 ? <span className="warn">落后 {item.behind} 集</span> : <span className="good">已跟上</span>}
        </div>
        <div className="today-actions">
          <button type="button" disabled={!canAdvance} onClick={() => void onProgress(item)}>{canAdvance ? "看完下一集" : "等待更新"}</button>
          <a className="inline-action" href={`https://search.bilibili.com/all?keyword=${encodeURIComponent(title)}`} target="_blank" rel="noreferrer">B站搜索</a>
          <button type="button" className="inline-action" onClick={() => void onPreference(item, { pinned: !item.pinned })}>{item.pinned ? "取消置顶" : "置顶"}</button>
          <button type="button" className="inline-action" onClick={() => void onPreference(item, { hidden_this_season: !hiddenView })}>{hiddenView ? "恢复" : "本季隐藏"}</button>
        </div>
      </div>
    </article>
  );
}
