"use client";

import Link from "next/link";
import {
  Download, ExternalLink, Eye, HeartHandshake, LoaderCircle, Plus, RefreshCw, Trash2, Users,
} from "lucide-react";
import { useEffect, useState } from "react";

import { AuthGate } from "../../components/auth-gate";
import { PageHeader } from "../../components/page-header";
import { productFetch } from "../../lib/api";
import { useExperience } from "../../lib/experience";

type Friend = { username: string; nickname?: string; created_at?: string; updated_at?: string };
type PulseItem = {
  subject_id: number; name: string; image?: string; count: number; friends: string[];
  avg_rate?: number; my_status?: string;
};
type MatrixItem = {
  username: string; nickname?: string; sync_score?: number; shrunk_score?: number;
  sync_level?: number; common_rated?: number; note?: string;
};
type FriendCollectionItem = {
  subject_id: number; name: string; image?: string; collection_type: number;
  rate?: number; ep_status?: number; eps?: number; updated_at?: string;
};
type FriendDetail = {
  friend: Friend; subject_type: string; watching: FriendCollectionItem[];
  wishlist: FriendCollectionItem[]; recent: FriendCollectionItem[]; total_public: number;
};

const media = [["anime", "动画"], ["book", "书籍"], ["game", "游戏"], ["music", "音乐"], ["real", "三次元"]] as const;

export default function FriendsPage() {
  const exp = useExperience();
  const [friends, setFriends] = useState<Friend[]>([]);
  const [data, setData] = useState<any>(null);
  const [detail, setDetail] = useState<FriendDetail | null>(null);
  const [username, setUsername] = useState("");
  const [subjectType, setSubjectType] = useState("anime");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    if (exp.authReady && exp.authenticated) void loadFriends();
  }, [exp.authReady, exp.authenticated]);

  async function loadFriends() {
    setError("");
    try {
      const payload = await productFetch("/workspace/friends");
      setFriends(payload.data || []);
    } catch (e) { setError(String(e)); }
  }

  async function addFriend() {
    if (!username.trim()) return;
    setBusy("add"); setError(""); setNotice("");
    try {
      await productFetch("/workspace/friends", {
        method: "POST",
        headers: { "Content-Type": "application/json", "x-otomo-csrf": exp.csrf },
        body: JSON.stringify({ username: username.trim().replace(/^@/, "") }),
      });
      setUsername(""); setNotice("已加入好友关注名单"); await loadFriends();
    } catch (e) { setError(String(e)); }
    finally { setBusy(""); }
  }

  async function importFriends() {
    setBusy("import"); setError(""); setNotice("");
    try {
      const payload = await productFetch("/workspace/friends/import", {
        method: "POST", headers: { "x-otomo-csrf": exp.csrf },
      }, { track: true, label: "导入 Bangumi 好友" });
      setFriends(payload.data || []);
      setNotice(`从 Bangumi 好友页导入 ${payload.imported || 0} 位；已有好友会自动合并。`);
    } catch (e) { setError(String(e)); }
    finally { setBusy(""); }
  }

  async function removeFriend(name: string) {
    setError("");
    try {
      await productFetch(`/workspace/friends/${encodeURIComponent(name)}`, {
        method: "DELETE", headers: { "x-otomo-csrf": exp.csrf },
      });
      setFriends((rows) => rows.filter((x) => x.username !== name)); setData(null);
      if (detail?.friend.username === name) setDetail(null);
    } catch (e) { setError(String(e)); }
  }

  async function analyze() {
    if (!friends.length) return;
    setBusy("analyze"); setError("");
    try {
      const payload = await productFetch(
        `/product/friends?subject_type=${subjectType}&limit=20`,
        undefined,
        { track: true, label: "分析好友圈" },
      );
      setData(payload.data);
    } catch (e) { setError(String(e)); }
    finally { setBusy(""); }
  }

  async function loadFriendDetail(name: string) {
    setBusy(`detail:${name}`); setError("");
    try {
      const payload = await productFetch(
        `/product/friends/${encodeURIComponent(name)}?subject_type=${subjectType}`,
      );
      setDetail(payload.data);
    } catch (e) { setError(String(e)); }
    finally { setBusy(""); }
  }

  return (
    <main className="page-frame friends-page">
      <PageHeader eyebrow="Friends" title="好友圈" description="把你真正关心的朋友放进名单，查看他们最近在追什么，以及谁和你的口味最接近。" actions={exp.authenticated && friends.length ? <button className="button-secondary icon-label" onClick={() => void analyze()} disabled={Boolean(busy)}><RefreshCw size={16} />更新好友圈</button> : undefined} />
      {!exp.authReady ? <div className="surface-loading">正在确认账户状态…</div> : null}
      {exp.authReady && !exp.authenticated ? <AuthGate eyebrow="SOCIAL TASTE" title="连接你的 Bangumi 好友圈" description="好友名单、同步率和公开追番动态都按当前账户隔离保存。" features={["导入公开好友列表", "查看好友都在追什么", "口味同步率与圈内高分"]} /> : null}
      {exp.authenticated ? <>
        <section className="friend-manager">
          <div>
            <span className="section-kicker">FOLLOWING</span>
            <h2>关注名单</h2>
            <p>名单属于 Otomo 工作区；可以从 Bangumi 导入，也可以单独关注任意公开用户。</p>
          </div>
          <div className="friend-add">
            <label><span>Bangumi 用户名</span><div><span>@</span><input value={username} onChange={(e) => setUsername(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") void addFriend(); }} placeholder="例如 sunshineclover" /></div></label>
            <button className="button-primary" disabled={!username.trim() || Boolean(busy)} onClick={() => void addFriend()}>{busy === "add" ? <LoaderCircle className="spin" size={16} /> : <Plus size={16} />}添加</button>
            <button className="button-secondary" disabled={Boolean(busy)} onClick={() => void importFriends()}>{busy === "import" ? <LoaderCircle className="spin" size={16} /> : <Download size={16} />}导入 Bangumi 好友</button>
          </div>
          {notice ? <div className="inline-notice">{notice}</div> : null}
          {error ? <div className="surface-error">{error}</div> : null}
          {friends.length ? <div className="friend-chips">{friends.map((friend) => <article key={friend.username}><span className="friend-avatar">{(friend.nickname || friend.username).slice(0, 1).toUpperCase()}</span><span><strong>{friend.nickname || `@${friend.username}`}</strong>{friend.nickname ? <small>@{friend.username}</small> : <small>Bangumi 用户</small>}</span><button className="icon-plain" onClick={() => void loadFriendDetail(friend.username)} title="查看公开追番">{busy === `detail:${friend.username}` ? <LoaderCircle className="spin" size={14} /> : <Eye size={14} />}</button><a className="icon-plain" href={`https://bgm.tv/user/${friend.username}`} target="_blank" rel="noreferrer" title="打开 Bangumi"><ExternalLink size={14} /></a><button className="icon-plain" onClick={() => void removeFriend(friend.username)} title="移出名单"><Trash2 size={14} /></button></article>)}</div> : <div className="friend-empty"><Users size={24} /><strong>还没有关注好友</strong><span>添加用户名，或从当前 Bangumi 账号的公开好友页一次导入。</span></div>}
        </section>

        {detail ? <FriendDetailPanel data={detail} /> : null}

        {friends.length ? <section className="workspace-section">
          <div className="section-heading"><div><span className="section-kicker">PULSE</span><h2>好友都在看什么</h2></div><div className="filter-row"><nav className="media-switch">{media.map(([value, label]) => <button key={value} className={subjectType === value ? "active" : ""} onClick={() => { setSubjectType(value); setData(null); setDetail(null); }}>{label}</button>)}</nav><button className="button-primary" onClick={() => void analyze()} disabled={Boolean(busy)}>{busy === "analyze" ? <LoaderCircle className="spin" size={16} /> : <HeartHandshake size={16} />}生成好友圈视图</button></div></div>
          {!data ? <div className="feature-empty compact"><HeartHandshake size={22} /><strong>名单已经准备好</strong><span>生成后会读取好友的公开收藏，聚合在追、想看、圈内高分和同步率。</span></div> : <FriendsDashboard data={data} />}
        </section> : null}
      </> : null}
    </main>
  );
}

function FriendDetailPanel({ data }: { data: FriendDetail }) {
  const rows: [string, FriendCollectionItem[]][] = [
    ["正在追", data.watching], ["想看", data.wishlist], ["最近更新", data.recent],
  ];
  return <section className="friend-detail workspace-section">
    <div className="section-heading"><div><span className="section-kicker">PUBLIC COLLECTION</span><h2>{data.friend.nickname || `@${data.friend.username}`} 的公开收藏</h2><p>@{data.friend.username} · {data.total_public} 项公开记录</p></div><a className="button-secondary icon-label" href={`https://bgm.tv/user/${data.friend.username}`} target="_blank" rel="noreferrer"><ExternalLink size={15} />Bangumi 主页</a></div>
    <div className="friend-detail-grid">{rows.map(([title, items]) => <section key={title}><header><h3>{title}</h3><span>{items.length}</span></header>{items.length ? <div>{items.slice(0, title === "最近更新" ? 12 : 20).map((item) => <Link className="friend-detail-item" href={`/subject/${item.subject_id}`} key={`${title}-${item.subject_id}`}>{item.image ? <img src={item.image} alt="" /> : <span className="friend-cover" />}<span><strong>{item.name}</strong><small>{item.eps ? `进度 ${item.ep_status || 0}/${item.eps}` : item.ep_status ? `进度 ${item.ep_status}` : "未记录进度"}{item.rate ? ` · ${item.rate} 分` : ""}</small></span></Link>)}</div> : <div className="friend-board-empty">没有公开记录</div>}</section>)}</div>
  </section>;
}

function FriendsDashboard({ data }: { data: any }) {
  const pulse = data.pulse || {};
  const boards: [string, PulseItem[], "count" | "rate"][] = [
    ["好友都在追", pulse.watching_hot || [], "count"],
    ["好友都想看", pulse.wishlist_hot || [], "count"],
    ["好友圈高分", pulse.top_rated || [], "rate"],
  ];
  return <div className="friends-dashboard">
    <div className="friend-pulse-grid">{boards.map(([title, items, metric]) => <section key={title}><header><h3>{title}</h3><span>{items.length} 部</span></header>{items.length ? <div>{items.slice(0, 8).map((item) => <Link className="friend-pulse-item" href={`/subject/${item.subject_id}`} key={item.subject_id}>{item.image ? <img src={item.image} alt="" /> : <span className="friend-cover" />}<span><strong>{item.name}</strong><small>{metric === "rate" && item.avg_rate ? `圈内均分 ${item.avg_rate}` : `${item.count} 位好友`}{item.my_status ? ` · 我：${item.my_status}` : ""}</small><i>{(item.friends || []).slice(0, 3).map((x) => `@${x}`).join("  ")}</i></span></Link>)}</div> : <div className="friend-board-empty">暂时没有可聚合的公开收藏</div>}</section>)}</div>
    <section className="friend-ranking"><header><div><span className="section-kicker">AFFINITY</span><h3>口味同步率</h3></div><small>共同评分少时会自动向中位收缩</small></header>{(data.matrix || []).length ? <div>{(data.matrix as MatrixItem[]).map((item, index) => <article key={item.username}><b>{index + 1}</b><span><strong>@{item.username}</strong><small>{item.note || `共同评分 ${item.common_rated || 0} 部`}</small></span>{item.shrunk_score != null ? <em>{item.shrunk_score}<small>Lv {item.sync_level}</small></em> : <em className="muted">--</em>}</article>)}</div> : <div className="friend-board-empty">公开评分样本不足</div>}</section>
    {data.caveats?.length ? <p className="friend-caveat">{data.caveats.join(" ")}</p> : null}
  </div>;
}
