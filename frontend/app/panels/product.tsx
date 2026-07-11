"use client";

// 产品聚合域面板：月报、作品档案、驾驶舱分区、音乐主题曲、周报。

import { type AnyRecord, type ShareSnapshotType, type ShareSnapshotHandler, list, text, Badge, Panel, EmptyHint, ShareSnapshotButton } from "./shared";

export function MonthlyWatchReportPanel({ data, onShareSnapshot }: { data: AnyRecord; onShareSnapshot?: ShareSnapshotHandler }) {
  const sections = list(data.sections);
  const byTitle = new Map(sections.map((s) => [String(s.title || ""), s]));
  const summary = data.summary || {};
  const metricRows = [
    ["收藏总量", summary.collection_count],
    ["本月更新", summary.month_updated_count],
    ["本月完成", summary.completed_this_month],
    ["本月均分", summary.month_avg_rate ?? "暂无"],
    ["全量均分", summary.avg_user_rate ?? "暂无"],
    ["已评分", summary.rated_count],
  ];
  const renderSubjectCards = (title: string, limit = 8) => {
    const items = list(byTitle.get(title)?.items);
    if (!items.length) return <EmptyHint text="暂无条目" />;
    return (
      <div className="rec-grid">
        {items.slice(0, limit).map((item, i) => (
          <a className="rec-card" href={item.id ? `https://bgm.tv/subject/${item.id}` : undefined} target={item.id ? "_blank" : undefined} rel={item.id ? "noreferrer" : undefined} key={`${title}-${item.id}-${i}`}>
            {item.image ? <img src={item.image} alt="" /> : <div className="rec-noimg" />}
            <div className="rec-body">
              <div className="card-title">{text(item.name)}</div>
              <div className="card-meta">
                {text(item.status, "")}
                {item.rate ? ` · 你 ${item.rate}` : ""}
                {item.score ? ` · BGM ${item.score}` : ""}
                {item.ep_status ? ` · ep ${item.ep_status}` : ""}
              </div>
              {item.comment ? <p className="card-note">{text(item.comment)}</p> : null}
              {item.updated_at ? <div className="card-meta">更新：{text(item.updated_at)}</div> : null}
            </div>
          </a>
        ))}
      </div>
    );
  };
  const renderCompact = (title: string, primary: string, secondary?: string, limit = 16) => {
    const items = list(byTitle.get(title)?.items);
    if (!items.length) return <EmptyHint text="暂无数据" />;
    return (
      <div className="compact-list">
        {items.slice(0, limit).map((item, i) => (
          <span key={`${title}-${i}`}>
            {text(item[primary] ?? item.name ?? item.status ?? item.rating)}
            {secondary && item[secondary] !== undefined ? ` · ${secondary} ${item[secondary]}` : ""}
            {item.count !== undefined ? ` · ${item.count}` : ""}
            {item.lift !== undefined ? ` · lift ${item.lift}` : ""}
          </span>
        ))}
      </div>
    );
  };
  return (
    <Panel title={`月度报告 · @${text(data.username)}`} subtitle={`${data.year}-${String(data.month || "").padStart(2, "0")} · ${text(data.subject_type)}`}>
      <div className="panel-actions">
        <ShareSnapshotButton
          type="monthly_report"
          title={`月度报告 · @${text(data.username)} · ${data.year}-${String(data.month || "").padStart(2, "0")}`}
          payload={data}
          onShareSnapshot={onShareSnapshot}
        />
      </div>
      <div className="metric-grid">
        {metricRows.map(([label, value]) => (
          <div className="metric-card" key={String(label)}>
            <div className="metric-label">{label}</div>
            <div className="metric-value">{text(value)}</div>
          </div>
        ))}
      </div>
      <div className="section-title">本月完成</div>
      {renderSubjectCards("本月完成", 8)}
      <div className="section-title">本月更新</div>
      {renderSubjectCards("本月更新", 8)}
      <div className="taste-groups">
        <div className="taste-group">
          <div className="section-title">状态分布</div>
          {renderCompact("状态分布", "status", "count")}
        </div>
        <div className="taste-group">
          <div className="section-title">评分分布</div>
          {renderCompact("评分分布", "rating", "count")}
        </div>
        <div className="taste-group">
          <div className="section-title">本月标签漂移</div>
          {renderCompact("本月标签漂移", "tag", "month_count")}
        </div>
        <div className="taste-group">
          <div className="section-title">Staff/CV/Studio</div>
          {renderCompact("Staff/CV/Studio", "name", "count")}
        </div>
      </div>
      <div className="section-title">搁置/抛弃观察</div>
      {renderSubjectCards("搁置/抛弃观察", 8)}
      {list<string>(data.caveats).length > 0 && (
        <div className="caveats">{list<string>(data.caveats).map((n, i) => <span key={i}>{n}</span>)}</div>
      )}
    </Panel>
  );
}

export function ProductSectionsPanel({
  data,
  title,
  shareType,
  onShareSnapshot,
}: {
  data: AnyRecord;
  title: string;
  shareType?: ShareSnapshotType;
  onShareSnapshot?: ShareSnapshotHandler;
}) {
  const subject = data.subject || data.seed || {};
  const sections = list(data.sections);
  const nodes = list(data.nodes);
  const edges = list(data.edges);
  const subscription = data.subscription || {};
  return (
    <Panel
      title={title}
      subtitle={subject.name ? text(subject.name) : data.username ? `@${text(data.username)}` : text(data.season || data.month || data.today, "")}
    >
      {shareType && (
        <div className="panel-actions">
          <ShareSnapshotButton
            type={shareType}
            title={`${title}${subject.name ? ` · ${text(subject.name)}` : ""}`}
            payload={data}
            onShareSnapshot={onShareSnapshot}
          />
        </div>
      )}
      {Object.keys(subject).length > 0 && (
        <div className="subject-hero compact">
          {subject.image ? <img src={subject.image} alt="" /> : null}
          <div>
            <div className="card-title">{text(subject.name)}</div>
            <div className="card-meta">
              {text(subject.type_name, "")} {subject.date ? `· ${subject.date}` : ""} {subject.score ? `· ${subject.score}` : ""}
            </div>
            {subject.summary ? <p className="card-note">{text(subject.summary)}</p> : null}
            <div className="evidence-row tight">
              {list<string>(subject.tags).slice(0, 8).map((tag) => <Badge key={tag} tone="dim">{tag}</Badge>)}
            </div>
          </div>
        </div>
      )}
      {Object.keys(subscription).length > 0 && (
        <div className="evidence-row">
          <Badge tone={subscription.enabled ? "good" : "dim"}>周报 {subscription.enabled ? "on" : "off"}</Badge>
          <Badge tone="dim">每日提醒走订阅中心</Badge>
          <Badge tone="dim">push {text(subscription.push_grading, "normal")}</Badge>
          {list<string>(subscription.channels).map((ch) => <Badge key={ch} tone="dim">{ch}</Badge>)}
          {subscription.webhook_format ? <Badge tone="dim">{subscription.webhook_format}</Badge> : null}
        </div>
      )}
      {sections.map((section, i) => (
        <div key={`${section.title}-${i}`}>
          <div className="section-title">{text(section.title)}</div>
          {list(section.items).length > 0 ? (
            <div className="rec-grid">
              {list(section.items).slice(0, 12).map((item, idx) => {
                const subjectId = item.subject_id || item.id;
                const href = subjectId ? `https://bgm.tv/subject/${subjectId}` : item.url || item.page_url || "";
                return (
                  <a
                    className="rec-card"
                    href={href || undefined}
                    target={href ? "_blank" : undefined}
                    rel={href ? "noreferrer" : undefined}
                    key={`${subjectId || section.title}-${idx}`}
                  >
                    {item.image ? <img src={item.image} alt="" /> : <div className="rec-noimg" />}
                    <div className="rec-body">
                      <div className="card-title">{text(item.name || item.title || item.anime_title || item.relation || item.source)}</div>
                      <div className="card-meta">
                        {item.status || item.type_name || item.relation || item.action || item.theme_type || ""}
                        {item.score ? ` · ${item.score}` : ""}
                        {item.rank ? ` · rank ${item.rank}` : ""}
                        {item.my_ep !== undefined ? ` · ep ${item.my_ep}` : ""}
                      </div>
                      {item.reason || item.note || item.consensus ? <p className="card-note">{text(item.reason || item.note || item.consensus)}</p> : null}
                      {list<string>(item.why).length > 0 && (
                        <div className="compact-list inline">{list<string>(item.why).slice(0, 3).map((x, j) => <span key={j}>{x}</span>)}</div>
                      )}
                      {list(item.peaks).length > 0 && (
                        <div className="compact-list inline">
                          {list(item.peaks).slice(0, 3).map((p, j) => <span key={j}>EP {p.episode || p.sort} · {p.comments} 讨论</span>)}
                        </div>
                      )}
                    </div>
                  </a>
                );
              })}
            </div>
          ) : <EmptyHint text="暂无数据" />}
          {list<string>(section.notes).length > 0 && (
            <div className="caveats">{list<string>(section.notes).map((n, j) => <span key={j}>{n}</span>)}</div>
          )}
        </div>
      ))}
      {nodes.length > 0 && (
        <>
          <div className="section-title">图谱节点</div>
          <div className="compact-list">
            {nodes.slice(0, 24).map((node) => (
              <span key={node.id}>{text(node.name)} · {text(node.type_name, "unknown")}{node.date ? ` · ${node.date}` : ""}</span>
            ))}
          </div>
        </>
      )}
      {edges.length > 0 && (
        <>
          <div className="section-title">关系边</div>
          <div className="compact-list">
            {edges.slice(0, 24).map((edge, i) => (
              <span key={`${edge.source}-${edge.target}-${i}`}>{edge.source} → {edge.target} · {text(edge.relation)}</span>
            ))}
          </div>
        </>
      )}
      {list<string>(data.quick_actions).length > 0 && (
        <>
          <div className="section-title">快捷动作</div>
          <div className="followups">{list<string>(data.quick_actions).map((q, i) => <span className="chip ghost" key={i}>{q}</span>)}</div>
        </>
      )}
      {list<string>(data.next_actions).length > 0 && (
        <>
          <div className="section-title">下一步</div>
          <div className="compact-list">{list<string>(data.next_actions).map((n, i) => <span key={i}>{n}</span>)}</div>
        </>
      )}
      {list<string>(data.caveats || data.notes).length > 0 && (
        <div className="caveats">{list<string>(data.caveats || data.notes).map((n, i) => <span key={i}>{n}</span>)}</div>
      )}
    </Panel>
  );
}

export function SubjectDossierPanel({ data, onShareSnapshot }: { data: AnyRecord; onShareSnapshot?: ShareSnapshotHandler }) {
  const subject = data.subject || {};
  const sections = list(data.sections);
  const byTitle = new Map(sections.map((s) => [String(s.title || ""), s]));
  const sectionNames = ["评价矩阵", "观看/购买入口", "OP/ED/音乐", "补番路线", "分集热度雷达", "跨媒体关系", "Release/RSS"];
  return (
    <Panel title="作品档案" subtitle={text(subject.name)}>
      <div className="panel-actions">
        <ShareSnapshotButton
          type="subject_dossier"
          title={`${text(subject.name)} 作品档案`}
          payload={data}
          onShareSnapshot={onShareSnapshot}
        />
      </div>
      <div className="subject-hero compact">
        {subject.image ? <img src={subject.image} alt="" /> : <div className="rec-noimg" />}
        <div>
          <div className="card-title">{text(subject.name)}</div>
          <div className="card-meta">
            {text(subject.type_name, "")} {subject.date ? `· ${subject.date}` : ""} {subject.score ? `· BGM ${subject.score}` : ""}
            {subject.rank ? ` · rank ${subject.rank}` : ""}
          </div>
          {subject.summary ? <p className="card-note">{text(subject.summary)}</p> : null}
          <div className="evidence-row tight">
            {list<string>(subject.tags).slice(0, 10).map((tag) => <Badge key={tag} tone="dim">{tag}</Badge>)}
          </div>
        </div>
      </div>
      <div className="dossier-grid">
        {sectionNames.map((name) => {
          const section = byTitle.get(name);
          if (!section) return null;
          const items = list(section.items);
          return (
            <div className="dossier-section" key={name}>
              <div className="section-title">{name}</div>
              {items.length ? (
                <div className="compact-list">
                  {items.slice(0, name === "OP/ED/音乐" ? 12 : 8).map((item, i) => {
                    if (name === "评价矩阵") {
                      return (
                        <span key={i}>
                          {text(item.consensus, "暂无综合评价")}
                          {list(item.ratings).length ? ` · ${list(item.ratings).length} 个评分源` : ""}
                          {list(item.aspect_summary).length ? ` · ${list(item.aspect_summary).length} 个口碑方面` : ""}
                        </span>
                      );
                    }
                    if (name === "观看/购买入口") {
                      const official = list(item.official_sources);
                      const fallback = list(item.search_fallbacks);
                      return (
                        <span key={i} className="stacked-line">
                          <b>正版/官方入口 {official.length} 个 · 兜底搜索 {fallback.length}</b>
                          {official.slice(0, 4).map((src, idx) => (
                            <a href={src.url} target="_blank" rel="noreferrer" key={`${src.url}-${idx}`}>
                              {text(src.label || src.site || src.source)}{src.regions ? ` · ${list<string>(src.regions).join("/")}` : ""}
                            </a>
                          ))}
                          {!official.length && fallback.slice(0, 3).map((src, idx) => (
                            <a href={src.url} target="_blank" rel="noreferrer" key={`${src.url}-${idx}`}>
                              {text(src.label || src.source)}
                            </a>
                          ))}
                        </span>
                      );
                    }
                    if (name === "Release/RSS") {
                      const groups = list(item.groups);
                      const fallback = list(item.fallback_items);
                      const links = list(item.search_links);
                      return (
                        <span key={i} className="stacked-line">
                          <b>RSS 组 {groups.length} 个 · fallback {fallback.length} 条 · 搜索入口 {links.length} 个</b>
                          {groups.slice(0, 4).map((group, idx) => (
                            <a href={group.rss_url || group.url || group.page_url} target="_blank" rel="noreferrer" key={`${group.source}-${group.subgroup}-${idx}`}>
                              RSS · {text(group.source)} {text(group.subgroup, "")}
                            </a>
                          ))}
                          {links.slice(0, 3).map((link, idx) => (
                            <a href={link.url} target="_blank" rel="noreferrer" key={`${link.url}-${idx}`}>
                              搜索 · {text(link.label || link.source)}
                            </a>
                          ))}
                        </span>
                      );
                    }
                    if (name === "OP/ED/音乐") {
                      return (
                        <span key={i}>
                          {text(item.kind, "music")} · {text(item.song_title || item.matched_bangumi_music_name)}
                          {list<string>(item.artists).length ? ` · ${list<string>(item.artists).slice(0, 3).join(" / ")}` : ""}
                          {item.matched_bangumi_music_id ? ` · BGM#${item.matched_bangumi_music_id}` : ""}
                        </span>
                      );
                    }
                    if (name === "分集热度雷达") {
                      const ep = item.ep || item.sort || item.episode || item.episode_sort || "?";
                      return <span key={i}>EP {ep} · {item.comments ?? 0} 讨论 · {text(item.name, "")}</span>;
                    }
                    if (name === "跨媒体关系") {
                      return <span key={i}>{text(item.relation)} · {text(item.name_cn || item.name)} · {text(item.type_name, "")}</span>;
                    }
                    if (name === "补番路线") {
                      const order = list(item.watch_order);
                      const sides = list(item.side_stories);
                      const skips = list(item.skip_candidates);
                      return (
                        <span key={i} className="stacked-line">
                          <b>主线 {order.length} 部 · 旁支 {sides.length} 部 · 可跳过 {skips.length} 部</b>
                          {order.slice(0, 5).map((x, idx) => (
                            <a href={x.id ? `https://bgm.tv/subject/${x.id}` : undefined} target="_blank" rel="noreferrer" key={`${x.id}-${idx}`}>
                              {idx + 1}. {text(x.name)}{x.necessity ? ` · ${text(x.necessity)}` : ""}{x.date ? ` · ${x.date}` : ""}
                            </a>
                          ))}
                          {sides.slice(0, 3).map((x, idx) => (
                            <a href={x.id ? `https://bgm.tv/subject/${x.id}` : undefined} target="_blank" rel="noreferrer" key={`side-${x.id}-${idx}`}>
                              旁支 · {text(x.name)}{x.necessity ? ` · ${text(x.necessity)}` : ""}
                            </a>
                          ))}
                          {skips.slice(0, 3).map((x, idx) => (
                            <span key={`skip-${x.id}-${idx}`}>可跳过 · {text(x.name)}{x.skip_advice ? ` · ${text(x.skip_advice)}` : ""}</span>
                          ))}
                        </span>
                      );
                    }
                    return <span key={i}>{text(item.consensus || item.title || item.name || item.label || item.source || JSON.stringify(item).slice(0, 80))}</span>;
                  })}
                </div>
              ) : <EmptyHint text="暂无数据" />}
              {list<string>(section.notes).length > 0 && (
                <div className="caveats">{list<string>(section.notes).slice(0, 2).map((n, i) => <span key={i}>{n}</span>)}</div>
              )}
            </div>
          );
        })}
      </div>
      {list<string>(data.quick_actions).length > 0 && (
        <div className="followups">{list<string>(data.quick_actions).map((q, i) => <span className="chip ghost" key={i}>{q}</span>)}</div>
      )}
      {list<string>(data.caveats).length > 0 && (
        <div className="caveats">{list<string>(data.caveats).map((n, i) => <span key={i}>{n}</span>)}</div>
      )}
    </Panel>
  );
}

export function AnimeMusicThemesPanel({ data }: { data: AnyRecord }) {
  const subject = data.subject || {};
  const fused = list(data.fused);
  return (
    <Panel title="OP/ED/音乐融合" subtitle={text(subject.name)}>
      <div className="evidence-row">
        <Badge tone="good">Bangumi music {list(data.bangumi_music).length}</Badge>
        <Badge tone="dim">AnimeThemes {list(data.animethemes_entries).length}</Badge>
      </div>
      <div className="rec-grid">
        {fused.map((item, i) => {
          const href = item.bangumi_url || item.animethemes_url || item.video_url || "";
          return (
            <a className="rec-card" href={href || undefined} target={href ? "_blank" : undefined} rel={href ? "noreferrer" : undefined} key={`${item.song_title}-${i}`}>
              <div className="rec-body">
                <div className="card-title">{text(item.song_title || item.matched_bangumi_music_name)}</div>
                <div className="card-meta">
                  {text(item.kind, "music")}
                  {item.theme_type ? ` · ${item.theme_type}${item.sequence ? ` ${item.sequence}` : ""}` : ""}
                  {item.score ? ` · BGM ${item.score}` : ""}
                </div>
                {list<string>(item.artists).length > 0 && (
                  <div className="compact-list inline">{list<string>(item.artists).slice(0, 4).map((a) => <span key={a}>{a}</span>)}</div>
                )}
                <p className="card-note">{text(item.mapping_note, "")}</p>
              </div>
            </a>
          );
        })}
      </div>
      {list<string>(data.notes).length > 0 && (
        <div className="caveats">{list<string>(data.notes).map((n, i) => <span key={i}>{n}</span>)}</div>
      )}
      {list<string>(data.caveats).length > 0 && (
        <div className="caveats">{list<string>(data.caveats).map((n, i) => <span key={i}>{n}</span>)}</div>
      )}
    </Panel>
  );
}

export function AnimeThemesPanel({ data }: { data: AnyRecord }) {
  const entries = list(data.entries);
  return (
    <Panel title="AnimeThemes 音乐元数据" subtitle={`${text(data.query)} · ${entries.length} 条`}>
      <div className="rec-grid">
        {entries.map((entry, i) => (
          <a className="rec-card" href={entry.page_url || entry.video_url} target="_blank" rel="noreferrer" key={`${entry.slug}-${i}`}>
            <div className="rec-body">
              <div className="card-title">{text(entry.song_title || entry.anime_title)}</div>
              <div className="card-meta">{text(entry.anime_title)} · {text(entry.theme_type)}{entry.sequence ? ` ${entry.sequence}` : ""}</div>
              <div className="compact-list inline">
                {list<string>(entry.artists).slice(0, 4).map((a) => <span key={a}>{a}</span>)}
              </div>
            </div>
          </a>
        ))}
      </div>
      {list<string>(data.notes).length > 0 && (
        <div className="caveats">{list<string>(data.notes).map((n, i) => <span key={i}>{n}</span>)}</div>
      )}
    </Panel>
  );
}

export function WeeklyDigestPanel({ data }: { data: AnyRecord }) {
  const sections = list(data.sections);
  return (
    <Panel title={`本周周报 · ${text(data.username)}`} subtitle={text(data.week, "本周")}>
      <div className="evidence-row">
        {list<string>(data.profile_tags).slice(0, 10).map((tag) => <Badge key={tag} tone="dim">{tag}</Badge>)}
      </div>
      {sections.map((section, i) => (
        <div key={`${section.title}-${i}`}>
          <div className="section-title">{text(section.title)}</div>
          <div className="rec-grid">
            {list(section.items).slice(0, 6).map((item, idx) => (
              <a className="rec-card" href={`https://bgm.tv/subject/${item.id}`} target="_blank" rel="noreferrer" key={`${item.id}-${idx}`}>
                {item.image ? <img src={item.image} alt="" /> : <div className="rec-noimg" />}
                <div className="rec-body">
                  <div className="card-title">{text(item.name)}</div>
                  <div className="card-meta">
                    {text(item.status)} · {text(item.action)} · BGM {item.bangumi_score ?? "暂无"}
                  </div>
                  <div className="compact-list inline">
                    {list<string>(item.why).slice(0, 3).map((why, j) => <span key={j}>{why}</span>)}
                  </div>
                </div>
              </a>
            ))}
          </div>
          {list<string>(section.notes).length > 0 && (
            <div className="caveats">{list<string>(section.notes).map((n, j) => <span key={j}>{n}</span>)}</div>
          )}
        </div>
      ))}
      {list<string>(data.next_actions).length > 0 && (
        <>
          <div className="section-title">下一步</div>
          <div className="compact-list">{list<string>(data.next_actions).map((n, i) => <span key={i}>{n}</span>)}</div>
        </>
      )}
      {list<string>(data.caveats).length > 0 && (
        <div className="caveats">{list<string>(data.caveats).map((n, i) => <span key={i}>{n}</span>)}</div>
      )}
    </Panel>
  );
}


