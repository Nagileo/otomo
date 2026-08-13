"use client";

import Link from "next/link";
import { Bookmark, ListPlus, Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

import { PageHeader } from "../../components/page-header";
import { AuthGate } from "../../components/auth-gate";
import { productFetch } from "../../lib/api";
import { useExperience } from "../../lib/experience";

type View = { id: string; name: string; surface: string; params: Record<string, any>; updated_at: string };
type ListItem = { subject_id: number; name: string; image?: string; subject_type: string; note?: string };
type CustomList = { id: string; title: string; description: string; items: ListItem[]; updated_at: string };

function viewHref(view: View) {
  const query = new URLSearchParams();
  Object.entries(view.params || {}).forEach(([key, value]) => {
    if (Array.isArray(value)) query.set(key, value.join(","));
    else if (value !== undefined && value !== null && value !== "") query.set(key, String(value));
  });
  return `${view.surface === "today" ? "/" : `/${view.surface}`}?${query.toString()}`;
}

export default function WorkspacePage() {
  const [views, setViews] = useState<View[]>([]);
  const [lists, setLists] = useState<CustomList[]>([]);
  const [csrf, setCsrf] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState("");
  const exp = useExperience();
  async function load() {
    try {
      const [v, l] = await Promise.all([productFetch("/workspace/views"), productFetch("/workspace/lists")]);
      setViews(v.data || []); setLists(l.data || []);
    } catch (e) { setError(String(e)); }
  }
  useEffect(() => {
    setCsrf(exp.csrf);
    if (exp.authReady && exp.authenticated) void load();
  }, [exp.authReady, exp.authenticated, exp.csrf]);
  async function createList() {
    if (!title.trim()) return;
    await productFetch("/workspace/lists", { method: "POST", headers: { "Content-Type": "application/json", "x-otomo-csrf": csrf }, body: JSON.stringify({ title: title.trim(), description: description.trim() }) });
    setTitle(""); setDescription(""); await load();
  }
  async function remove(kind: "views" | "lists", id: string) {
    await productFetch(`/workspace/${kind}/${id}`, { method: "DELETE", headers: { "x-otomo-csrf": csrf } }); await load();
  }
  async function removeItem(listId: string, subjectId: number) {
    await productFetch(`/workspace/lists/${listId}/items/${subjectId}`, { method: "DELETE", headers: { "x-otomo-csrf": csrf } }); await load();
  }
  return (
    <main className="page-frame workspace-page">
      <PageHeader eyebrow="Workspace" title="我的工作区" description="保存常用发现条件，把跨媒介作品整理成真正属于你的候选清单。" />
      {!exp.authReady ? <div className="surface-loading">正在确认账户状态…</div> : null}
      {exp.authReady && !exp.authenticated ? <AuthGate eyebrow="ACCOUNT WORKSPACE" title="为你的收藏建立私人工作区" description="连接后，清单和保存视图会按 Bangumi 账户隔离，在不同设备登录同一账户即可恢复。" features={["跨媒介自定义清单", "发现条件一键保存", "账户级好友关注名单"]} /> : null}
      {exp.authenticated && error ? <div className="surface-error">{error}</div> : null}
      {exp.authenticated ? <>
        <section className="workspace-section"><div className="section-heading"><div><span className="section-kicker">SAVED VIEWS</span><h2>保存视图</h2></div></div>{views.length ? <div className="saved-view-grid">{views.map((view) => <article key={view.id}><Bookmark size={17} /><span><Link href={viewHref(view)}>{view.name}</Link><small>{view.surface} · {Object.entries(view.params).map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join("/") : v}`).join(" · ")}</small></span><button className="icon-plain" title="删除" onClick={() => void remove("views", view.id)}><Trash2 size={15} /></button></article>)}</div> : <div className="feature-empty compact"><Bookmark size={21} /><strong>还没有保存视图</strong><span>在发现页设置媒介、场景和标签后保存，之后可以一键恢复。</span></div>}</section>
        <section className="workspace-section"><div className="section-heading"><div><span className="section-kicker">LISTS</span><h2>自定义清单</h2></div></div><div className="list-create"><label><span>清单名称</span><input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="例如：周末两小时看完" /></label><label><span>说明</span><input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="可选，说明选片标准" /></label><button className="button-primary" onClick={() => void createList()} disabled={!title.trim()}><Plus size={16} />新建清单</button></div><div className="custom-list-grid">{lists.map((list) => <section key={list.id}><header><span><strong>{list.title}</strong><small>{list.description || `${list.items.length} 个条目`}</small></span><button className="icon-plain" title="删除清单" onClick={() => void remove("lists", list.id)}><Trash2 size={15} /></button></header>{list.items.length ? <div className="custom-list-items">{list.items.map((item) => <article key={item.subject_id}>{item.image ? <img src={item.image} alt="" /> : <span className="list-cover" />}<span><Link href={`/subject/${item.subject_id}`}>{item.name || `Subject ${item.subject_id}`}</Link><small>{item.subject_type}{item.note ? ` · ${item.note}` : ""}</small></span><button className="icon-plain" onClick={() => void removeItem(list.id, item.subject_id)} title="移出清单"><XIcon /></button></article>)}</div> : <div className="list-empty"><ListPlus size={19} />从作品档案页将条目加入这里。</div>}</section>)}</div></section>
      </> : null}
    </main>
  );
}

function XIcon() { return <span aria-hidden="true">×</span>; }
