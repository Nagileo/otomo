"use client";

import {
  Brain, Download, LoaderCircle, Plus, RotateCcw, Save, ShieldCheck, Trash2,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { AuthGate } from "../../components/auth-gate";
import { PageHeader } from "../../components/page-header";
import { BACKEND, productFetch } from "../../lib/api";
import { useExperience } from "../../lib/experience";

type Row = Record<string, any>;
type MemoryData = {
  username: string;
  likes: Row[];
  dislikes: Row[];
  spoiler_default: "none" | "mild" | "full";
  progress: Record<string, Row>;
  feedback: Row[];
  aspect_profiles: Record<string, Row>;
  counts: { explicit: number; derived: number; profile: number; progress: number };
  updated_at: string;
};

const SOURCE_LABEL: Record<string, string> = {
  explicit_user: "你明确告诉 Otomo",
  derived_from_feedback: "根据反馈推导",
  bangumi_profile: "来自 Bangumi 收藏",
};

function sourceLabel(source: string) {
  return SOURCE_LABEL[source] || "历史记忆";
}

export default function MemoryPage() {
  const exp = useExperience();
  const [data, setData] = useState<MemoryData | null>(null);
  const [busy, setBusy] = useState(true);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");
  const [newLike, setNewLike] = useState("");
  const [newDislike, setNewDislike] = useState("");

  useEffect(() => {
    void exp.refreshAuthSession().then((auth) => {
      if (auth.authenticated) void load();
      else setBusy(false);
    });
  }, []);

  async function load() {
    setBusy(true);
    try {
      const payload = await productFetch("/memory");
      setData(payload.data);
    } catch (error) {
      setNotice(String(error));
    } finally {
      setBusy(false);
    }
  }

  async function save(patch: Partial<MemoryData>, message = "记忆已更新") {
    setSaving(true);
    setNotice("");
    try {
      const payload = await productFetch("/memory", {
        method: "PATCH",
        headers: { "Content-Type": "application/json", "x-otomo-csrf": exp.csrf },
        body: JSON.stringify(patch),
      });
      setData(payload.data);
      setNotice(message);
    } catch (error) {
      setNotice(String(error));
    } finally {
      setSaving(false);
    }
  }

  async function clear(
    category: "likes" | "dislikes" | "progress" | "feedback" | "derived" | "all",
    label: string,
  ) {
    if (!window.confirm(`确定要${label}吗？这会立即影响后续推荐和对话。`)) return;
    setSaving(true);
    try {
      const payload = await productFetch(`/memory/${category}`, {
        method: "DELETE", headers: { "x-otomo-csrf": exp.csrf },
      });
      setData(payload.data);
      setNotice(`${label}完成`);
    } catch (error) {
      setNotice(String(error));
    } finally {
      setSaving(false);
    }
  }

  async function exportMemory() {
    const response = await fetch(`${BACKEND}/memory/export`, { credentials: "include" });
    if (!response.ok) { setNotice("导出失败，请重新登录后再试"); return; }
    const payload = await response.json();
    const url = URL.createObjectURL(new Blob(
      [JSON.stringify(payload, null, 2)], { type: "application/json" },
    ));
    const link = document.createElement("a");
    link.href = url;
    link.download = `otomo-memory-${data?.username || "export"}.json`;
    link.click();
    URL.revokeObjectURL(url);
  }

  const aspects = useMemo(() => Object.entries(data?.aspect_profiles || {}), [data]);
  if (!exp.authReady || busy) return <main className="page-frame"><div className="surface-loading"><LoaderCircle className="spin" size={17} /> 正在读取记忆…</div></main>;
  if (!exp.authenticated) return <main className="page-frame"><AuthGate eyebrow="记忆管理" title="先连接 Bangumi" description="记忆只属于你的账号；登录后才能查看、修改与导出。" features={["明确偏好与推导偏好分开", "可以随时修正或清除", "支持完整导出"]} /></main>;
  if (!data) return <main className="page-frame"><div className="surface-error">{notice || "暂时无法读取记忆"}<button className="button-secondary" onClick={() => void load()}>重试</button></div></main>;
  const memory = data;

  function preferenceSection(
    kind: "likes" | "dislikes", title: string, draft: string,
    setDraft: (value: string) => void,
  ) {
    const rows = memory[kind];
    return (
      <section className="memory-section">
        <header><div><h2>{title}</h2><p>这些内容会直接参与推荐排序与回答措辞。</p></div><button className="button-quiet danger" onClick={() => void clear(kind, `清空${title}`)} disabled={!rows.length || saving}><Trash2 size={15} />清空</button></header>
        <div className="memory-add"><input value={draft} maxLength={120} onChange={(event) => setDraft(event.target.value)} placeholder={`添加一条${title}`} /><button className="button-secondary" disabled={!draft.trim() || saving} onClick={() => { const item = { value: draft.trim(), source: "explicit_user", confidence: 1, ts: new Date().toISOString() }; void save({ [kind]: [...rows, item] }, `${title}已添加`); setDraft(""); }}><Plus size={15} />添加</button></div>
        <div className="memory-list">
          {rows.map((item, index) => <div className="memory-row" key={`${item.value}-${index}`}><div><input aria-label={`${title}内容`} value={item.value} onChange={(event) => setData({ ...memory, [kind]: rows.map((row, i) => i === index ? { ...row, value: event.target.value, source: "explicit_user", confidence: 1 } : row) })} /><small>{sourceLabel(item.source)} · 置信度 {Math.round((item.confidence || 0) * 100)}%</small></div><button className="icon-plain" title="删除" onClick={() => void save({ [kind]: rows.filter((_, i) => i !== index) })}><Trash2 size={15} /></button></div>)}
          {!rows.length ? <div className="feature-empty">还没有这类记忆。你可以在这里添加，也可以在对话中直接告诉 Otomo。</div> : null}
        </div>
        {rows.length ? <button className="button-secondary memory-save" disabled={saving} onClick={() => void save({ [kind]: rows })}><Save size={15} />保存编辑</button> : null}
      </section>
    );
  }

  return (
    <main className="page-frame memory-page">
      <PageHeader eyebrow="隐私与个性化" title="Otomo 记住了什么" description="明确偏好、观看进度和系统推导分开展示；你拥有查看、修正、导出与删除权。" />
      <div className="memory-toolbar">
        <div className="memory-stat"><strong>{data.counts.explicit}</strong><span>明确记忆</span></div>
        <div className="memory-stat"><strong>{data.counts.derived}</strong><span>推导信号</span></div>
        <div className="memory-stat"><strong>{data.counts.progress}</strong><span>进度记录</span></div>
        <div className="memory-actions"><button className="button-secondary" onClick={() => void exportMemory()}><Download size={15} />导出 JSON</button><button className="button-secondary" onClick={() => void load()}><RotateCcw size={15} />重新读取</button></div>
      </div>
      {notice ? <div className="inline-notice memory-notice">{notice}</div> : null}
      <div className="memory-grid">
        {preferenceSection("likes", "喜欢", newLike, setNewLike)}
        {preferenceSection("dislikes", "不喜欢", newDislike, setNewDislike)}
      </div>
      <section className="memory-section">
        <header><div><h2>防剧透与观看进度</h2><p>默认防剧透级别是长期偏好；每次对话仍可以临时覆盖。</p></div></header>
        <label className="memory-spoiler"><span>默认防剧透</span><select value={data.spoiler_default} onChange={(event) => void save({ spoiler_default: event.target.value as MemoryData["spoiler_default"] })}><option value="none">不剧透</option><option value="mild">轻微剧透</option><option value="full">允许完整剧情</option></select></label>
        <div className="memory-progress-grid">
          {Object.entries(data.progress).map(([name, item]) => <label className="memory-progress" key={name}><span><strong>{name}</strong><small>{sourceLabel(item.source)}</small></span><input type="number" min={0} value={item.episode || 0} onChange={(event) => setData({ ...data, progress: { ...data.progress, [name]: { ...item, episode: Number(event.target.value), source: "explicit_user", confidence: 1 } } })} /><button className="icon-plain" title="删除进度" onClick={(event) => { event.preventDefault(); const progress = { ...data.progress }; delete progress[name]; void save({ progress }); }}><Trash2 size={15} /></button></label>)}
          {!Object.keys(data.progress).length ? <div className="feature-empty">暂无由对话记住的观看进度。Bangumi 收藏仍会在需要时实时读取。</div> : null}
        </div>
        {Object.keys(data.progress).length ? <button className="button-secondary memory-save" onClick={() => void save({ progress: data.progress })}><Save size={15} />保存进度</button> : null}
      </section>
      <section className="memory-section derived">
        <header><div><h2>系统推导与反馈</h2><p>这部分不是你直接说出的事实，而是从“更多/更少/不感兴趣”等反馈提炼出的弱信号。</p></div><button className="button-quiet danger" disabled={saving || (!aspects.length && !data.feedback.length)} onClick={() => void clear("derived", "清除所有推导记忆")}><Trash2 size={15} />清除推导</button></header>
        <div className="memory-derived-note"><ShieldCheck size={18} /><span>推导信号置信度较低，不会覆盖你明确写下的偏好。发现不准时可以整批清除。</span></div>
        <div className="memory-aspects">{aspects.flatMap(([media, profile]) => [...(profile.likes || []), ...(profile.dislikes || [])].map((item: Row, index: number) => <span key={`${media}-${item.aspect}-${index}`} className={item.polarity === "dislike" ? "negative" : "positive"}>{item.label || item.aspect}<small>{media} · {Math.round((item.confidence || 0) * 100)}%</small></span>))}</div>
        <details className="memory-feedback"><summary>查看 {data.feedback.length} 条推荐反馈</summary><div>{data.feedback.slice().reverse().map((item, index) => <p key={`${item.subject_id}-${index}`}><strong>{item.name || `条目 ${item.subject_id || ""}`}</strong><span>{item.signal} · {item.scope} · {sourceLabel(item.source)}</span>{item.note ? <small>{item.note}</small> : null}</p>)}</div></details>
      </section>
      <section className="memory-danger-zone"><Brain size={20} /><div><strong>重置个性化记忆</strong><span>清除偏好、进度、反馈、画像缓存和推导结果；不会删除收藏、订阅、清单或通知。</span></div><button className="button-secondary danger" disabled={saving} onClick={() => void clear("all", "重置个性化记忆")}><Trash2 size={15} />全部重置</button></section>
    </main>
  );
}
