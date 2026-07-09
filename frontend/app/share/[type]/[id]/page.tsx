import type { Metadata } from "next";

const BACKEND = process.env.NEXT_PUBLIC_BACKEND ?? "http://localhost:8000";

type AnyRecord = Record<string, any>;

function list<T = AnyRecord>(value: any): T[] {
  return Array.isArray(value) ? value : [];
}

function text(value: any, fallback = "未知") {
  const s = String(value ?? "").trim();
  return s || fallback;
}

async function getSnapshot(id: string) {
  const res = await fetch(`${BACKEND}/share/snapshots/${encodeURIComponent(id)}`, {
    next: { revalidate: 30 },
  });
  if (!res.ok) return null;
  const payload = await res.json().catch(() => ({}));
  return payload?.snapshot || null;
}

export async function generateMetadata({ params }: { params: { id: string; type: string } }): Promise<Metadata> {
  const snapshot = await getSnapshot(params.id);
  if (!snapshot) {
    return { title: "分享页不存在 · Otomo" };
  }
  return {
    title: `${snapshot.title} · Otomo`,
    description: snapshot.summary || "Otomo ACGN 产品页分享",
    openGraph: {
      title: `${snapshot.title} · Otomo`,
      description: snapshot.summary || "Otomo ACGN 产品页分享",
      type: "article",
      images: firstImage(snapshot.payload) ? [{ url: firstImage(snapshot.payload) }] : undefined,
    },
  };
}

export default async function ShareSnapshotPage({ params }: { params: { id: string; type: string } }) {
  const snapshot = await getSnapshot(params.id);
  if (!snapshot || snapshot.type !== params.type) {
    return (
      <main className="share-page">
        <section className="share-empty">
          <h1>分享页不存在</h1>
          <p>这个链接可能已撤销、过期，或类型不匹配。</p>
          <a href="/">回到 Otomo</a>
        </section>
      </main>
    );
  }
  return (
    <main className="share-page">
      <header className="share-hero">
        <div>
          <div className="share-kicker">Otomo · 番组搭子</div>
          <h1>{text(snapshot.title, "Otomo 分享页")}</h1>
          {snapshot.summary ? <p>{snapshot.summary}</p> : null}
          <div className="share-badges">
            <span>{text(snapshot.type)}</span>
            <span>schema v{snapshot.schema_version ?? 1}</span>
            <span>剧透 {text(snapshot.spoiler_level, "none")}</span>
            <span>{snapshot.personalized ? "脱敏个性化" : "泛化公开"}</span>
            {snapshot.created_at ? <span>{String(snapshot.created_at).slice(0, 10)}</span> : null}
          </div>
        </div>
      </header>
      {snapshot.spoiler_level !== "none" && (
        <section className="share-warning">
          当前分享页包含 {snapshot.spoiler_level} 级剧透内容，默认不展开剧透细节。
        </section>
      )}
      <ShareBody type={snapshot.type} payload={snapshot.payload || {}} />
      <ShareSources snapshot={snapshot} />
    </main>
  );
}

function ShareBody({ type, payload }: { type: string; payload: AnyRecord }) {
  if (type === "subject_dossier") return <SubjectDossierShare data={payload} />;
  if (type === "watch_order") return <WatchOrderShare data={payload} />;
  if (type === "monthly_report") return <MonthlyReportShare data={payload} />;
  if (type === "season_guide") return <SeasonGuideShare data={payload} />;
  if (type === "watch_cockpit") return <GenericSectionsShare data={payload} title="追番驾驶舱" />;
  return <GenericSectionsShare data={payload} title="Otomo 分享页" />;
}

function SubjectDossierShare({ data }: { data: AnyRecord }) {
  const subject = data.subject || {};
  const sections = list(data.sections);
  return (
    <>
      <SubjectHero subject={subject} />
      <section className="share-grid">
        {["评价矩阵", "观看/购买入口", "OP/ED/音乐", "补番路线", "分集热度雷达", "跨媒体关系", "Release/RSS"].map((name) => {
          const section = sections.find((s) => s.title === name);
          return section ? <ShareSection key={name} section={section} /> : null;
        })}
      </section>
      <Caveats items={data.caveats} />
    </>
  );
}

function WatchOrderShare({ data }: { data: AnyRecord }) {
  const groups = [
    ["主线顺序", "watch_order"],
    ["OVA / 番外 / 旁支", "side_stories"],
    ["总集篇可跳过候选", "skip_candidates"],
    ["不同演绎 / 重制 / 替代路线", "alternate_routes"],
  ];
  return (
    <section className="share-section">
      <h2>{text(data.ip, "系列")} 补番路线</h2>
      {groups.map(([label, key]) => (
        <div className="share-subsection" key={key}>
          <h3>{label}</h3>
          <div className="share-list">
            {list(data[key]).map((item, i) => (
              <a href={item.id ? `https://bgm.tv/subject/${item.id}` : undefined} key={`${key}-${item.id}-${i}`}>
                <b>{item.order ?? i + 1}. {text(item.name)}</b>
                <span>{text(item.necessity || item.relation || item.watch_role, "")}{item.duration_hint ? ` · ${item.duration_hint}` : ""}</span>
                {item.skip_advice ? <small>{item.skip_advice}</small> : null}
              </a>
            ))}
          </div>
        </div>
      ))}
      <Caveats items={data.notes || data.caveats} />
    </section>
  );
}

function MonthlyReportShare({ data }: { data: AnyRecord }) {
  const summary = data.summary || {};
  const sections = list(data.sections);
  const metrics = [
    ["收藏总量", summary.collection_count],
    ["本月更新", summary.month_updated_count],
    ["本月完成", summary.completed_this_month],
    ["本月均分", summary.month_avg_rate ?? "暂无"],
    ["全量均分", summary.avg_user_rate ?? "暂无"],
    ["已评分", summary.rated_count],
  ];
  return (
    <>
      <section className="share-section">
        <h2>@{text(data.username)} · {data.year}-{String(data.month || "").padStart(2, "0")}</h2>
        <div className="share-metrics">
          {metrics.map(([label, value]) => (
            <div key={label}>
              <span>{label}</span>
              <b>{text(value)}</b>
            </div>
          ))}
        </div>
      </section>
      <section className="share-grid">
        {sections.map((section, i) => <ShareSection section={section} key={`${section.title}-${i}`} />)}
      </section>
      <Caveats items={data.caveats} />
    </>
  );
}

function SeasonGuideShare({ data }: { data: AnyRecord }) {
  const items = list(data.items);
  const guides = list(data.guide_videos);
  return (
    <>
      <section className="share-section">
        <h2>{text(data.season, "季度")} 新番导视</h2>
        <div className="share-badges compact">
          <span>{text(data.mode, "guide")}</span>
          {list<string>(data.profile_tags).slice(0, 8).map((tag) => <span key={tag}>{tag}</span>)}
        </div>
        <div className="share-card-grid">
          {items.map((item, i) => (
            <a href={`https://bgm.tv/subject/${item.subject_id}`} key={`${item.subject_id}-${i}`}>
              {item.image ? <img src={item.image} alt="" /> : <div className="share-noimg" />}
              <b>{text(item.title)}</b>
              <span>{item.bangumi_score ? `BGM ${item.bangumi_score}` : "暂无评分"}{item.broadcast ? ` · ${item.broadcast}` : ""}</span>
              <small>{text(item.reason, "")}</small>
            </a>
          ))}
        </div>
      </section>
      {guides.length > 0 && (
        <section className="share-section">
          <h2>圈层导视源</h2>
          <div className="share-list">
            {guides.map((video, i) => {
              const hit = list(video.verified_hits)[0] || {};
              const href = hit.url || video.url || video.up_url;
              return (
                <a href={href} key={`${video.up_name}-${i}`}>
                  <b>{text(video.up_name)}</b>
                  <span>{video.verified ? "已命中具体视频" : "仅导航入口"} · {text(video.positioning)}</span>
                  <small>{text(hit.title || video.verification_note || video.match_reason, "")}</small>
                </a>
              );
            })}
          </div>
        </section>
      )}
      <Caveats items={data.caveats || data.notes} />
    </>
  );
}

function GenericSectionsShare({ data, title }: { data: AnyRecord; title: string }) {
  const subject = data.subject || data.seed || {};
  const sections = list(data.sections);
  return (
    <>
      {Object.keys(subject).length > 0 ? <SubjectHero subject={subject} /> : null}
      <section className="share-grid">
        {sections.length ? sections.map((section, i) => <ShareSection section={section} key={`${section.title}-${i}`} />) : (
          <section className="share-section"><h2>{title}</h2><pre>{JSON.stringify(data, null, 2)}</pre></section>
        )}
      </section>
      <Caveats items={data.caveats || data.notes} />
    </>
  );
}

function SubjectHero({ subject }: { subject: AnyRecord }) {
  return (
    <section className="share-subject-hero">
      {subject.image ? <img src={subject.image} alt="" /> : <div className="share-noimg" />}
      <div>
        <h2>{text(subject.name)}</h2>
        <p>{text(subject.summary, "")}</p>
        <div className="share-badges compact">
          {subject.type_name ? <span>{subject.type_name}</span> : null}
          {subject.date ? <span>{subject.date}</span> : null}
          {subject.score ? <span>BGM {subject.score}</span> : null}
          {subject.rank ? <span>rank {subject.rank}</span> : null}
          {list<string>(subject.tags).slice(0, 10).map((tag) => <span key={tag}>{tag}</span>)}
        </div>
      </div>
    </section>
  );
}

function ShareSection({ section }: { section: AnyRecord }) {
  const items = list(section.items);
  const title = text(section.title, "Section");
  if (title === "Release/RSS") return <ShareReleaseSection section={section} />;
  if (title === "补番路线" && items[0]) return <ShareWatchOrderSection data={items[0]} notes={section.notes} />;
  if (title === "分集热度雷达") return <ShareEpisodeRadarSection section={section} />;
  return (
    <section className="share-section">
      <h2>{title}</h2>
      {items.length ? (
        <div className="share-list">
          {items.slice(0, 16).map((item, i) => (
            <ShareItem item={item} key={`${section.title}-${i}`} />
          ))}
        </div>
      ) : (
        <p className="share-dim">暂无数据</p>
      )}
      <Caveats items={section.notes} />
    </section>
  );
}

function ShareReleaseSection({ section }: { section: AnyRecord }) {
  const payload = list(section.items)[0] || {};
  const groups = list(payload.groups);
  const links = list(payload.search_links);
  const fallback = list(payload.fallback_items);
  return (
    <section className="share-section">
      <h2>Release / RSS</h2>
      <div className="share-list">
        {groups.slice(0, 10).map((group, i) => (
          <a href={group.rss_url || group.url || group.page_url || undefined} key={`${group.source}-${group.subgroup}-${i}`}>
            <b>RSS · {text(group.source)} {text(group.subgroup, "")}</b>
            <span>{text(group.quality, "tv")}{group.latest_items ? ` · 最近 ${list(group.latest_items).length} 条` : ""}</span>
            {group.rss_url ? <small>{group.rss_url}</small> : null}
          </a>
        ))}
        {links.slice(0, 8).map((link, i) => (
          <a href={link.url || undefined} key={`${link.url}-${i}`}>
            <b>搜索入口 · {text(link.label || link.source)}</b>
            <span>{text(link.note, "")}</span>
          </a>
        ))}
        {!groups.length && !links.length && fallback.slice(0, 8).map((item, i) => (
          <a href={item.page_url || item.torrent_url || undefined} key={`${item.title}-${i}`}>
            <b>{text(item.title)}</b>
            <span>{text(item.source, "")}{item.subgroup ? ` · ${item.subgroup}` : ""}</span>
          </a>
        ))}
      </div>
      <Caveats items={section.notes || payload.caveats} />
    </section>
  );
}

function ShareWatchOrderSection({ data, notes }: { data: AnyRecord; notes?: any }) {
  const groups = [
    ["主线顺序", "watch_order"],
    ["OVA / 番外 / 旁支", "side_stories"],
    ["总集篇可跳过候选", "skip_candidates"],
    ["不同演绎 / 重制 / 替代路线", "alternate_routes"],
  ];
  return (
    <section className="share-section">
      <h2>{text(data.ip, "系列")} 补番路线</h2>
      {groups.map(([label, key]) => {
        const rows = list(data[key]);
        if (!rows.length) return null;
        return (
          <div className="share-subsection" key={key}>
            <h3>{label}</h3>
            <div className="share-list">
              {rows.map((item, i) => (
                <a href={item.id ? `https://bgm.tv/subject/${item.id}` : undefined} key={`${key}-${item.id}-${i}`}>
                  <b>{item.order ?? i + 1}. {text(item.name)}</b>
                  <span>{text(item.necessity || item.relation || item.watch_role, "")}{item.duration_hint ? ` · ${item.duration_hint}` : ""}</span>
                  {item.skip_advice || item.reason ? <small>{text(item.skip_advice || item.reason)}</small> : null}
                </a>
              ))}
            </div>
          </div>
        );
      })}
      <Caveats items={notes || data.notes || data.caveats} />
    </section>
  );
}

function ShareEpisodeRadarSection({ section }: { section: AnyRecord }) {
  const items = list(section.items);
  return (
    <section className="share-section">
      <h2>分集热度雷达</h2>
      <div className="share-list">
        {items.slice(0, 16).map((item, i) => {
          const ep = item.ep || item.sort || item.episode || item.episode_sort || "?";
          return (
            <span key={`${ep}-${i}`}>
              <b>EP {ep} · {text(item.name, "")}</b>
              <span>{item.comments ?? 0} 讨论</span>
              {list<string>(item.discussion).length ? <small>{list<string>(item.discussion).slice(0, 2).join(" / ")}</small> : null}
            </span>
          );
        })}
      </div>
      <Caveats items={section.notes} />
    </section>
  );
}

function ShareItem({ item }: { item: AnyRecord }) {
  if (typeof item === "string") return <span>{item}</span>;
  const href = item.id ? `https://bgm.tv/subject/${item.id}` : item.url || item.page_url || item.bangumi_url || item.animethemes_url || "";
  const title = item.name || item.title || item.song_title || item.consensus || item.relation || item.source || item.status || item.rating || item.tag;
  return (
    <a href={href || undefined}>
      <b>{text(title, "条目")}</b>
      <span>
        {item.score ? `score ${item.score}` : ""}
        {item.rank ? ` · rank ${item.rank}` : ""}
        {item.count !== undefined ? ` · ${item.count}` : ""}
        {item.lift !== undefined ? ` · lift ${item.lift}` : ""}
        {item.kind ? ` · ${item.kind}` : ""}
        {item.relation ? ` · ${item.relation}` : ""}
      </span>
      {item.note || item.reason || item.mapping_note ? <small>{text(item.note || item.reason || item.mapping_note)}</small> : null}
    </a>
  );
}

function ShareSources({ snapshot }: { snapshot: AnyRecord }) {
  const sources = list(snapshot.sources);
  return (
    <footer className="share-footer">
      <div>
        <h2>Sources / Caveats</h2>
        <p>由 Otomo 生成。分享页经过脱敏处理，外链仅用于追溯来源。</p>
        {snapshot.redaction?.removed_paths?.length ? (
          <p>脱敏：{snapshot.redaction.removed_paths.length} 个字段已移除或替换。</p>
        ) : null}
      </div>
      {sources.length > 0 && (
        <div className="share-source-list">
          {sources.slice(0, 16).map((source, i) => (
            <a href={source.url} key={`${source.url}-${i}`}>{text(source.source, "source")} · {text(source.title)}</a>
          ))}
        </div>
      )}
    </footer>
  );
}

function Caveats({ items }: { items: any }) {
  const rows = list<string>(items);
  if (!rows.length) return null;
  return (
    <div className="share-caveats">
      {rows.slice(0, 8).map((item, i) => <span key={i}>{item}</span>)}
    </div>
  );
}

function firstImage(value: any): string {
  if (!value || typeof value !== "object") return "";
  if (typeof value.image === "string" && value.image.startsWith("http")) return value.image;
  if (Array.isArray(value)) {
    for (const item of value) {
      const hit = firstImage(item);
      if (hit) return hit;
    }
  } else {
    for (const item of Object.values(value)) {
      const hit = firstImage(item);
      if (hit) return hit;
    }
  }
  return "";
}
