"use client";

import Link from "next/link";
import {
  Check, Download, ExternalLink, Eye, HeartHandshake, LoaderCircle, Plus, RefreshCw,
  Search, Trash2, Users, X,
} from "lucide-react";
import { useEffect, useState } from "react";

import { AuthGate } from "../../components/auth-gate";
import { PageHeader } from "../../components/page-header";
import { productFetch } from "../../lib/api";
import { useExperience } from "../../lib/experience";

type Friend = { username: string; nickname?: string; avatar_url?: string; created_at?: string; updated_at?: string };
type FriendCandidate = Friend & { url?: string; saved?: boolean };
type PulseItem = {
  subject_id: number; name: string; image?: string; count: number; friends: string[];
  avg_rate?: number; weighted_avg_rate?: number; weighted_score?: number;
  friend_weights?: { username: string; weight: number }[]; my_status?: string;
};
type MatrixItem = {
  username: string; nickname?: string; sync_score?: number; shrunk_score?: number;
  sync_level?: number; peer_weight?: number; common_rated?: number; note?: string;
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
  const [importOpen, setImportOpen] = useState(false);
  const [importError, setImportError] = useState("");
  const [importCandidates, setImportCandidates] = useState<FriendCandidate[] | null>(null);
  const [importSearch, setImportSearch] = useState("");
  const [selectedImports, setSelectedImports] = useState<Set<string>>(new Set());
  const [selectedOnly, setSelectedOnly] = useState(false);

  useEffect(() => {
    if (exp.authReady && exp.authenticated) void loadFriends();
  }, [exp.authReady, exp.authenticated]);

  useEffect(() => {
    if (!importOpen && !detail) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && busy !== "import") closeImportPicker();
      if (event.key === "Escape" && detail) setDetail(null);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [importOpen, detail, busy]);

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

  async function openImportPicker() {
    setImportOpen(true); setImportCandidates(null); setSelectedImports(new Set()); setImportSearch(""); setSelectedOnly(false);
    setBusy("import-preview"); setError(""); setImportError(""); setNotice("");
    try {
      const payload = await productFetch("/workspace/friends/import", {
        method: "GET",
      }, { track: true, label: "读取 Bangumi 好友" });
      setImportCandidates(payload.data || []);
    } catch (e) { setImportError(readableError("读取 Bangumi 好友", e)); }
    finally { setBusy(""); }
  }

  function closeImportPicker() {
    if (busy === "import") return;
    setImportOpen(false); setImportCandidates(null); setSelectedImports(new Set());
    setImportSearch(""); setImportError(""); setSelectedOnly(false);
  }

  async function importSelectedFriends() {
    if (!selectedImports.size) return;
    setBusy("import"); setError(""); setNotice("");
    try {
      const payload = await productFetch("/workspace/friends/import", {
        method: "POST",
        headers: { "Content-Type": "application/json", "x-otomo-csrf": exp.csrf },
        body: JSON.stringify({ usernames: [...selectedImports] }),
      }, { track: true, label: "导入选中的 Bangumi 好友" });
      setFriends(payload.data || []);
      setImportOpen(false);
      setImportCandidates(null);
      setSelectedImports(new Set());
      setNotice(`已加入 ${payload.imported || 0} 位好友；只处理了你明确勾选的人。`);
    } catch (e) { setImportError(readableError("导入选中的 Bangumi 好友", e)); }
    finally { setBusy(""); }
  }

  function toggleImport(name: string) {
    setSelectedImports((current) => {
      const next = new Set(current);
      if (next.has(name)) next.delete(name); else next.add(name);
      return next;
    });
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

  async function clearFriends() {
    if (!friends.length || !window.confirm(`确定清空当前 ${friends.length} 位好友吗？这个操作不会影响 Bangumi 好友关系。`)) return;
    setBusy("clear"); setError(""); setNotice("");
    try {
      const payload = await productFetch("/workspace/friends", {
        method: "DELETE", headers: { "x-otomo-csrf": exp.csrf },
      });
      setFriends([]); setData(null); setDetail(null);
      setNotice(`已清空 ${payload.deleted || 0} 位好友。`);
    } catch (e) { setError(String(e)); }
    finally { setBusy(""); }
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

  const importQuery = importSearch.trim().toLowerCase();
  const visibleCandidates = (importCandidates || []).filter((friend) => (
    !selectedOnly || selectedImports.has(friend.username)
  ) && (!importQuery
    || friend.username.toLowerCase().includes(importQuery)
    || (friend.nickname || "").toLowerCase().includes(importQuery)));
  const selectableVisible = visibleCandidates.filter((friend) => !friend.saved);
  const availableCount = (importCandidates || []).filter((friend) => !friend.saved).length;

  return (
    <main className="page-frame friends-page">
      <PageHeader eyebrow="Friends" title="好友圈" description="把你真正关心的朋友放进名单，查看他们最近在追什么，以及谁和你的口味最接近。" actions={exp.authenticated && friends.length ? <button className="button-secondary icon-label" onClick={() => void analyze()} disabled={Boolean(busy)}><RefreshCw size={16} />更新好友圈</button> : undefined} />
      {!exp.authReady ? <div className="surface-loading">正在确认账户状态…</div> : null}
      {exp.authReady && !exp.authenticated ? <AuthGate eyebrow="SOCIAL TASTE" title="连接你的 Bangumi 好友圈" description="好友名单、同步率和公开追番动态都按当前账户隔离保存。" features={["导入公开好友列表", "查看好友都在追什么", "口味同步率与圈内高分"]} /> : null}
      {exp.authenticated ? <>
        <section className="friend-manager">
          <div className="friend-manager-heading">
            <div>
              <span className="section-kicker">FOLLOWING</span>
              <h2>关注名单</h2>
              <p>名单属于 Otomo 工作区；从 Bangumi 读取候选后，由你逐个选择要关注的人。</p>
            </div>
            {friends.length ? <button className="button-danger subtle" disabled={Boolean(busy)} onClick={() => void clearFriends()}>{busy === "clear" ? <LoaderCircle className="spin" size={15} /> : <Trash2 size={15} />}清空名单</button> : null}
          </div>
          <div className="friend-add">
            <label><span>Bangumi 用户名</span><div><span>@</span><input value={username} onChange={(e) => setUsername(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") void addFriend(); }} placeholder="例如 sunshineclover" /></div></label>
            <button className="button-primary" disabled={!username.trim() || Boolean(busy)} onClick={() => void addFriend()}>{busy === "add" ? <LoaderCircle className="spin" size={16} /> : <Plus size={16} />}添加</button>
            <button className="button-secondary" disabled={Boolean(busy)} onClick={() => void openImportPicker()}>{busy === "import-preview" ? <LoaderCircle className="spin" size={16} /> : <Download size={16} />}从 Bangumi 选择</button>
          </div>
          {notice ? <div className="inline-notice">{notice}</div> : null}
          {error ? <div className="surface-error">{error}</div> : null}
          {friends.length ? <div className="friend-chips">{friends.map((friend) => <article key={friend.username}>{friend.avatar_url ? <img className="friend-avatar" src={friend.avatar_url} alt="" /> : <span className="friend-avatar">{(friend.nickname || friend.username).slice(0, 1).toUpperCase()}</span>}<span><strong>{friend.nickname || `@${friend.username}`}</strong>{friend.nickname ? <small>@{friend.username}</small> : <small>Bangumi 用户</small>}</span><button className="icon-plain" onClick={() => void loadFriendDetail(friend.username)} title="查看公开追番">{busy === `detail:${friend.username}` ? <LoaderCircle className="spin" size={14} /> : <Eye size={14} />}</button><a className="icon-plain" href={`https://bgm.tv/user/${friend.username}`} target="_blank" rel="noreferrer" title="打开 Bangumi"><ExternalLink size={14} /></a><button className="icon-plain" onClick={() => void removeFriend(friend.username)} title="移出名单"><Trash2 size={14} /></button></article>)}</div> : <div className="friend-empty"><Users size={24} /><strong>还没有关注好友</strong><span>添加用户名，或从 Bangumi 好友候选中勾选你真正关心的人。</span></div>}
        </section>

        {friends.length ? <section className="workspace-section">
          <div className="section-heading"><div><span className="section-kicker">PULSE</span><h2>好友都在看什么</h2></div><div className="filter-row"><nav className="media-switch">{media.map(([value, label]) => <button key={value} className={subjectType === value ? "active" : ""} onClick={() => { setSubjectType(value); setData(null); setDetail(null); }}>{label}</button>)}</nav><button className="button-primary" onClick={() => void analyze()} disabled={Boolean(busy)}>{busy === "analyze" ? <LoaderCircle className="spin" size={16} /> : <HeartHandshake size={16} />}生成好友圈视图</button></div></div>
          {!data ? <div className="feature-empty compact"><HeartHandshake size={22} /><strong>名单已经准备好</strong><span>生成后会读取好友的公开收藏，聚合在追、想看、圈内高分和同步率。</span></div> : <FriendsDashboard data={data} />}
        </section> : null}
        {importOpen ? <div className="global-overlay friend-import-overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) closeImportPicker(); }}>
          <section className="global-modal wide friend-import-modal" role="dialog" aria-modal="true" aria-labelledby="friend-import-title">
            <header className="global-modal-head">
              <div><strong id="friend-import-title">从 Bangumi 选择好友</strong><span>默认不选任何人，只有你勾选的好友会加入 Otomo 关注名单。</span></div>
              <button className="icon-plain" disabled={busy === "import"} onClick={closeImportPicker} title="关闭好友选择"><X size={18} /></button>
            </header>
            <div className="friend-import-picker">
              {busy === "import-preview" ? <div className="friend-import-loading"><LoaderCircle className="spin" size={20} /><strong>正在读取 Bangumi 好友…</strong><span>候选较多时可能需要几秒钟</span></div> : null}
              {importError ? <div className="friend-import-error"><div className="surface-error">{importError}</div><button className="button-secondary" onClick={() => void openImportPicker()}>重新读取</button></div> : null}
              {importCandidates ? <>
                <div className="friend-import-toolbar">
                  <label><Search size={15} /><input autoFocus value={importSearch} onChange={(event) => setImportSearch(event.target.value)} placeholder="搜索昵称或用户名" /></label>
                  <span>可选 {availableCount} · 已选 <strong>{selectedImports.size}</strong></span>
                  <button className={`button-secondary compact${selectedOnly ? " active" : ""}`} disabled={(!selectedImports.size && !selectedOnly) || Boolean(busy)} onClick={() => setSelectedOnly((value) => !value)}>{selectedOnly ? "查看全部" : "只看已选"}</button>
                  <button className="button-secondary compact" disabled={!selectableVisible.length || Boolean(busy)} onClick={() => { const visibleNames = selectableVisible.map((friend) => friend.username); const allSelected = visibleNames.every((name) => selectedImports.has(name)); setSelectedImports((current) => { const next = new Set(current); for (const name of visibleNames) { if (allSelected) next.delete(name); else next.add(name); } return next; }); }}>{selectableVisible.length && selectableVisible.every((friend) => selectedImports.has(friend.username)) ? "取消当前结果" : "选择当前结果"}</button>
                  <button className="button-secondary compact" disabled={!selectedImports.size || Boolean(busy)} onClick={() => { setSelectedImports(new Set()); setSelectedOnly(false); }}>清空选择</button>
                </div>
                {visibleCandidates.length ? <div className="friend-candidate-grid">
                  {visibleCandidates.map((friend) => {
                    const selected = selectedImports.has(friend.username);
                    return <label className={`${selected ? "selected" : ""}${friend.saved ? " saved" : ""}`} key={friend.username}>
                      <input type="checkbox" checked={Boolean(friend.saved || selected)} disabled={Boolean(friend.saved || busy)} onChange={() => toggleImport(friend.username)} />
                      {friend.avatar_url ? <img className="friend-avatar" src={friend.avatar_url} alt="" /> : <span className="friend-avatar">{(friend.nickname || friend.username).slice(0, 1).toUpperCase()}</span>}
                      <span><strong>{friend.nickname || `@${friend.username}`}</strong><small>@{friend.username}</small></span>
                      <i>{friend.saved ? "已在名单" : selected ? <Check size={15} /> : "选择"}</i>
                    </label>;
                  })}
                </div> : <div className="friend-import-empty">没有匹配的好友</div>}
                <footer><span>Bangumi 共返回 {importCandidates.length} 位候选</span><button className="button-primary" disabled={!selectedImports.size || Boolean(busy)} onClick={() => void importSelectedFriends()}>{busy === "import" ? <LoaderCircle className="spin" size={16} /> : <Check size={16} />}加入选中的 {selectedImports.size} 位</button></footer>
              </> : null}
            </div>
          </section>
        </div> : null}
        {detail ? <div className="global-overlay friend-detail-overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setDetail(null); }}><section className="global-modal wide friend-detail-modal" role="dialog" aria-modal="true" aria-labelledby="friend-detail-title"><header className="global-modal-head"><div><strong id="friend-detail-title">{detail.friend.nickname || `@${detail.friend.username}`} 的公开收藏</strong><span>@{detail.friend.username} · {detail.total_public} 项公开记录</span></div><button className="icon-plain" onClick={() => setDetail(null)} title="关闭好友详情"><X size={18} /></button></header><FriendDetailPanel data={detail} onRemove={(name) => void removeFriend(name)} /></section></div> : null}
      </> : null}
    </main>
  );
}

function readableError(action: string, error: unknown) {
  const message = error instanceof Error ? error.message : String(error);
  if (/failed to fetch|network\s*error|load failed/i.test(message)) return `${action}失败，请检查网络后重试。`;
  const detail = message.replace(/^Error:\s*/i, "").trim();
  return detail ? `${action}失败：${detail}` : `${action}失败，请稍后重试。`;
}

function FriendDetailPanel({ data, onRemove }: { data: FriendDetail; onRemove: (name: string) => void }) {
  const rows: [string, FriendCollectionItem[]][] = [
    ["正在追", data.watching], ["想看", data.wishlist], ["最近更新", data.recent],
  ];
  return <section className="friend-detail">
    <div className="friend-detail-actions"><span>以下只读取该好友在 Bangumi 公开展示的收藏。</span><div><a className="button-secondary icon-label" href={`https://bgm.tv/user/${data.friend.username}`} target="_blank" rel="noreferrer"><ExternalLink size={15} />Bangumi 主页</a><button className="button-secondary danger" onClick={() => onRemove(data.friend.username)}><Trash2 size={15} />移出关注名单</button></div></div>
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
    <div className="friend-pulse-grid">{boards.map(([title, items, metric]) => <section key={title}><header><h3>{title}</h3><span>{items.length} 部</span></header>{items.length ? <div>{items.slice(0, 8).map((item) => <Link className="friend-pulse-item" href={`/subject/${item.subject_id}`} key={item.subject_id}>{item.image ? <img src={item.image} alt="" /> : <span className="friend-cover" />}<span><strong>{item.name}</strong><small>{metric === "rate" && (item.weighted_avg_rate || item.avg_rate) ? `加权均分 ${item.weighted_avg_rate || item.avg_rate}` : `${item.count} 位好友 · 相似支持 ${Number(item.weighted_score || 0).toFixed(2)}`}{item.my_status ? ` · 我：${item.my_status}` : ""}</small><i>{(item.friend_weights || []).slice(0, 3).map((x: any) => `@${x.username} ${Number(x.weight || 0).toFixed(2)}`).join("  ") || (item.friends || []).slice(0, 3).map((x) => `@${x}`).join("  ")}</i></span></Link>)}</div> : <div className="friend-board-empty">暂时没有可聚合的公开收藏</div>}</section>)}</div>
    <section className="friend-ranking"><header><div><span className="section-kicker">AFFINITY</span><h3>口味同步率</h3></div><small>共同评分少时会自动向中位收缩；推荐使用右侧亲和权重</small></header>{(data.matrix || []).length ? <div>{(data.matrix as MatrixItem[]).map((item: any, index) => <article key={item.username}><b>{index + 1}</b><span><strong>@{item.username}</strong><small>{item.note || `共同评分 ${item.common_rated || 0} 部 · 推荐权重 ${Number(item.peer_weight || 0).toFixed(2)}`}</small></span>{item.shrunk_score != null ? <em>{item.shrunk_score}<small>Lv {item.sync_level}</small></em> : <em className="muted">--</em>}</article>)}</div> : <div className="friend-board-empty">公开评分样本不足</div>}</section>
    {data.caveats?.length ? <p className="friend-caveat">{data.caveats.join(" ")}</p> : null}
  </div>;
}
