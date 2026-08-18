"use client";

import { BarChart3, ExternalLink, Flag, LogIn, MessageSquareText, Send, Settings, Trash2, Users } from "lucide-react";
import { useEffect, useState } from "react";
import Link from "next/link";

import { PageHeader } from "../../components/page-header";
import { UserAvatar } from "../../components/identity-avatar";
import { BACKEND, readJson } from "../../lib/api";
import { useExperience } from "../../lib/experience";

type CommunityStats = {
  total_visitors?: number;
  visitors_today?: number;
  total_views?: number;
  views_today?: number;
  comment_count?: number;
  tracking_since?: string;
  popular_pages?: { path: string; views: number }[];
  privacy?: string;
};

type CommunityComment = {
  id: string;
  display_name: string;
  avatar_url?: string;
  content: string;
  created_at: string;
  edited?: boolean;
  can_delete?: boolean;
  can_report?: boolean;
  reported?: boolean;
  report_count?: number;
};

const PAGE_NAMES: Record<string, string> = {
  "/": "今日",
  "/chat": "对话",
  "/discover": "发现",
  "/library": "收藏",
  "/workspace": "清单",
  "/me": "我的",
  "/friends": "好友圈",
  "/community": "同好留言",
  "/settings/subscriptions": "订阅设置",
  "/subject": "作品档案",
  "/share": "公开分享",
};

function formatTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat("zh-CN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(date);
}

export default function CommunityPage() {
  const { authenticated, authReady, username, avatarUrl, csrf } = useExperience();
  const [stats, setStats] = useState<CommunityStats>({});
  const [comments, setComments] = useState<CommunityComment[]>([]);
  const [content, setContent] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [isAdmin, setIsAdmin] = useState(false);

  async function load() {
    try {
      const overview = await readJson(await fetch(`${BACKEND}/community`, { credentials: "include" }));
      setStats(overview.stats || {});
      setComments(Array.isArray(overview.comments) ? overview.comments : []);
      setIsAdmin(Boolean(overview.is_admin));
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  useEffect(() => { if (authReady) void load(); }, [authReady]);

  async function submitComment() {
    const clean = content.trim();
    if (!clean || busy) return;
    setBusy(true);
    try {
      const payload = await readJson(await fetch(`${BACKEND}/community/comments`, {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          ...(csrf ? { "x-otomo-csrf": csrf } : {}),
        },
        body: JSON.stringify({ content: clean }),
      }));
      setComments((rows) => [payload.comment, ...rows]);
      setStats(payload.stats || stats);
      setContent("");
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  async function deleteComment(comment: CommunityComment) {
    if (!comment.can_delete || busy || !window.confirm("删除这条留言？")) return;
    setBusy(true);
    try {
      const payload = await readJson(await fetch(`${BACKEND}/community/comments/${encodeURIComponent(comment.id)}`, {
        method: "DELETE",
        credentials: "include",
        headers: csrf ? { "x-otomo-csrf": csrf } : {},
      }));
      setComments((rows) => rows.filter((row) => row.id !== comment.id));
      setStats(payload.stats || stats);
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  async function reportComment(comment: CommunityComment) {
    if (!comment.can_report || busy) return;
    const reason = window.prompt("请简单说明举报原因（可留空）：", "不适当内容");
    if (reason === null) return;
    setBusy(true);
    try {
      await readJson(await fetch(`${BACKEND}/community/comments/${encodeURIComponent(comment.id)}/reports`, {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          ...(csrf ? { "x-otomo-csrf": csrf } : {}),
        },
        body: JSON.stringify({ reason: reason.trim() }),
      }));
      setComments((rows) => rows.map((row) => row.id === comment.id
        ? { ...row, can_report: false, reported: true }
        : row));
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="page-frame community-page">
      <PageHeader
        eyebrow="同好交流"
        title="同好留言"
        description="看看大家最近在用 Otomo 做什么，也可以留下建议、体验或想要的能力。"
        actions={isAdmin ? <Link className="button-secondary icon-label" href="/admin"><Settings size={15} />管理后台</Link> : null}
      />

      {error ? <div className="surface-error">{error}</div> : null}

      <section className="community-stats" aria-label="访客统计">
        <article><Users size={18} /><span>独立访客</span><strong>{stats.total_visitors ?? "—"}</strong><small>按浏览器会话 · 今日 {stats.visitors_today ?? "—"}</small></article>
        <article><BarChart3 size={18} /><span>页面浏览量</span><strong>{stats.total_views ?? "—"}</strong><small>同一会话 / 页面每小时计一次 · 今日 {stats.views_today ?? "—"}</small></article>
        <article><MessageSquareText size={18} /><span>公开留言</span><strong>{stats.comment_count ?? comments.length}</strong><small>连接 Bangumi 后可参与</small></article>
      </section>
      <p className="community-stat-scope">自本次统计部署起{stats.tracking_since ? `（${formatTime(stats.tracking_since)} 开始）` : ""}；不是 Bangumi 全站数据，也不保存原始 IP。</p>

      <div className="community-layout">
        <section className="community-feed">
          <div className="section-heading compact">
            <div><span className="section-kicker">公开留言板</span><h2>最近留言</h2></div>
          </div>
          {authenticated ? (
            <div className="comment-composer">
              <UserAvatar className="comment-avatar" username={username} avatarUrl={avatarUrl} />
              <div>
                <strong>@{username}</strong>
                <textarea
                  value={content}
                  onChange={(event) => setContent(event.target.value.slice(0, 500))}
                  placeholder="说说你的体验、建议，或者希望 Otomo 接下来做什么…"
                  rows={4}
                />
                <div><small>{content.length}/500</small><button className="button-primary icon-label" onClick={() => void submitComment()} disabled={busy || !content.trim()}><Send size={15} />发布</button></div>
              </div>
            </div>
          ) : (
            <div className="community-login-note">
              <span>连接 Bangumi 后即可留言；Otomo 只用授权身份确认作者，不会读取密码。</span>
              <a className="button-primary icon-label" href={`${BACKEND}/auth/bangumi/start?return_to=${encodeURIComponent("/community")}`}><LogIn size={15} />连接 Bangumi</a>
            </div>
          )}

          <div className="comment-list">
            {comments.length ? comments.map((comment) => (
              <article className="comment-row" key={comment.id}>
                <a href={`https://bgm.tv/user/${encodeURIComponent(comment.display_name)}`} target="_blank" rel="noreferrer" title={`打开 @${comment.display_name} 的 Bangumi 主页`}>
                  <UserAvatar className="comment-avatar" username={comment.display_name} avatarUrl={comment.avatar_url} />
                </a>
                <div>
                  <header>
                    <a className="comment-author" href={`https://bgm.tv/user/${encodeURIComponent(comment.display_name)}`} target="_blank" rel="noreferrer"><strong>@{comment.display_name}</strong><ExternalLink size={11} /></a>
                    <time>{formatTime(comment.created_at)}{comment.edited ? " · 已编辑" : ""}</time>
                    {typeof comment.report_count === "number" && comment.report_count > 0 ? <span className="report-count">{comment.report_count} 次举报</span> : null}
                    <span className="comment-actions">
                      {comment.can_report ? <button className="icon-plain" title="举报留言" onClick={() => void reportComment(comment)} disabled={busy}><Flag size={14} /></button> : null}
                      {comment.reported ? <span className="reported-label">已举报</span> : null}
                      {comment.can_delete ? <button className="icon-plain danger" title="删除留言" onClick={() => void deleteComment(comment)} disabled={busy}><Trash2 size={14} /></button> : null}
                    </span>
                  </header>
                  <p>{comment.content}</p>
                </div>
              </article>
            )) : <div className="feature-empty"><MessageSquareText size={28} /><strong>还没有留言</strong><span>成为第一个留下建议的同好。</span></div>}
          </div>
        </section>

        <aside className="community-insights">
          <h2>大家常去</h2>
          <div className="popular-pages">
            {(stats.popular_pages || []).map((item) => (
              <div key={item.path}><span>{PAGE_NAMES[item.path] || item.path}</span><strong>{item.views}</strong></div>
            ))}
            {!stats.popular_pages?.length ? <span className="muted-copy">统计会从本次部署后开始累积。</span> : null}
          </div>
          <p>{stats.privacy || "只展示聚合统计，不展示个人访问记录。"}</p>
        </aside>
      </div>
    </main>
  );
}
