"use client";

// 报告域面板：口味报告（含卡片导出）、收藏仪表盘。

import { useState } from "react";
import { type AnyRecord, list, text, pct, Badge, Panel, EmptyHint , Meta } from "./shared";

export function _wrapText(ctx: CanvasRenderingContext2D, content: string, x: number, y: number, maxW: number, lh: number): number {
  let line = "";
  for (const ch of String(content)) {
    if (ctx.measureText(line + ch).width > maxW && line) {
      ctx.fillText(line, x, y); line = ch; y += lh;
    } else line += ch;
  }
  if (line) { ctx.fillText(line, x, y); y += lh; }
  return y;
}

export function exportTasteCard(data: AnyRecord): void {
  const canvas = document.createElement("canvas");
  canvas.width = 600; canvas.height = 760;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  ctx.fillStyle = "#0f1117"; ctx.fillRect(0, 0, 600, 760);
  ctx.fillStyle = "#c9a3ff"; ctx.font = "bold 30px sans-serif";
  ctx.fillText("Otomo · 二次元人格卡", 40, 64);
  ctx.fillStyle = "#ffffff"; ctx.font = "bold 24px sans-serif";
  ctx.fillText(`@${text(data.username, "用户")}`, 40, 108);
  ctx.fillStyle = "#9aa4b2"; ctx.font = "16px sans-serif";
  let y = _wrapText(ctx, text(data.share_summary, ""), 40, 146, 520, 24) + 16;
  ctx.fillStyle = "#86efac"; ctx.font = "16px sans-serif";
  y = _wrapText(ctx, "标签：" + (list<string>(data.report_tags).join(" · ") || "—"), 40, y, 520, 24) + 20;
  const sec = list(data.sections)[0] as AnyRecord | undefined;
  if (sec) {
    ctx.fillStyle = "#ffffff"; ctx.font = "bold 19px sans-serif";
    ctx.fillText(`${text(sec.subject_type)} · 看过 ${sec.watched ?? 0} · 均分 ${sec.avg_rating ?? "-"}`, 40, y); y += 32;
    ctx.fillStyle = "#9aa4b2"; ctx.font = "15px sans-serif";
    y = _wrapText(ctx, text(sec.persona, ""), 40, y, 520, 22) + 14;
    const likes = list(sec.aspect_likes).map((x: AnyRecord) => text(x.label || x.aspect)).slice(0, 3);
    const dislikes = list(sec.aspect_dislikes).map((x: AnyRecord) => text(x.label || x.aspect)).slice(0, 3);
    ctx.fillStyle = "#86efac"; ctx.fillText("好球区：" + (likes.join("、") || "—"), 40, y); y += 28;
    ctx.fillStyle = "#fca5a5"; ctx.fillText("雷区：" + (dislikes.join("、") || "—"), 40, y); y += 36;
  }
  ctx.fillStyle = "#6b7280"; ctx.font = "13px sans-serif";
  ctx.fillText("由 Otomo · 番组搭子 生成", 40, 730);
  const a = document.createElement("a");
  a.href = canvas.toDataURL("image/png");
  a.download = `otomo-taste-${text(data.username, "card")}.png`;
  a.click();
}

export function TasteReportPanel({ data }: { data: AnyRecord }) {
  const sections = list(data.sections);
  return (
    <Panel title={`口味报告 · ${text(data.username)}`} subtitle={text(data.share_summary, "")}>
      <div className="evidence-row">
        {list<string>(data.report_tags).map((tag) => <Badge key={tag} tone="good">{tag}</Badge>)}
        <button className="chip" onClick={() => exportTasteCard(data)}>📷 导出人格卡</button>
      </div>
      <div className="rating-grid">
        {sections.map((section, i) => (
          <div className="rating-card" key={`${section.subject_type}-${i}`}>
            <div className="rating-source">{text(section.subject_type)}</div>
            <div className="card-meta">
              看过 {section.watched ?? 0} · 评分 {section.rated ?? 0} · 均分 {section.avg_rating ?? "暂无"}
            </div>
            <p className="card-note">{text(section.persona, "")}</p>
            <div className="evidence-row tight">
              {list(section.top_tags).slice(0, 5).map((tag) => <Badge key={tag.tag} tone="dim">{text(tag.tag)}</Badge>)}
            </div>
            <div className="evidence-row tight">
              {list(section.aspect_likes).slice(0, 3).map((item) => <Badge key={`l-${item.aspect}`} tone="good">好球 {text(item.label || item.aspect)}</Badge>)}
              {list(section.aspect_dislikes).slice(0, 3).map((item) => <Badge key={`d-${item.aspect}`} tone="warn">雷区 {text(item.label || item.aspect)}</Badge>)}
            </div>
            {list<string>(section.next_actions).length > 0 && (
              <div className="compact-list">
                {list<string>(section.next_actions).map((x, idx) => <span key={idx}>{x}</span>)}
              </div>
            )}
          </div>
        ))}
      </div>
      {(list(data.global_likes).length > 0 || list(data.global_dislikes).length > 0) && (
        <div className="memory-grid">
          <div>
            <div className="section-title">长期喜欢</div>
            <div className="compact-list">{list(data.global_likes).slice(0, 8).map((x, i) => <span key={i}>{text(x.value)}</span>)}</div>
          </div>
          <div>
            <div className="section-title">长期避雷</div>
            <div className="compact-list">{list(data.global_dislikes).slice(0, 8).map((x, i) => <span key={i}>{text(x.value)}</span>)}</div>
          </div>
        </div>
      )}
      <Meta notes={list<string>(data.caveats)} />
    </Panel>
  );
}

export function DistributionBadges({ data }: { data: AnyRecord }) {
  const entries = Object.entries(data || {}).slice(0, 10);
  if (!entries.length) return <span className="card-meta">暂无</span>;
  return (
    <div className="evidence-row tight">
      {entries.map(([key, value]) => <Badge key={key} tone="dim">{key}: {String(value)}</Badge>)}
    </div>
  );
}

export function SubjectMiniList({ title, items }: { title: string; items: AnyRecord[] }) {
  if (!items.length) return null;
  return (
    <div>
      <div className="section-title">{title}</div>
      <div className="compact-subject-grid">
        {items.slice(0, 8).map((item, i) => (
          <a
            key={`${item.id || item.name}-${i}`}
            className="compact-subject"
            href={item.id ? `https://bgm.tv/subject/${item.id}` : undefined}
            target="_blank"
            rel="noreferrer"
          >
            {item.image ? <img src={item.image} alt="" loading="lazy" /> : <span className="shared-noimg" />}
            <span>
              <strong>{text(item.name)}</strong>
              <small>{item.rate ? `评分 ${item.rate}` : text(item.status, "")}{item.ep_status ? ` · ep ${item.ep_status}` : ""}</small>
            </span>
          </a>
        ))}
      </div>
    </div>
  );
}

export function YearlyActivityList({ items }: { items: AnyRecord[] }) {
  if (!items.length) return null;
  return (
    <div>
      <div className="section-title">年度活动</div>
      <div className="compact-list">
        {items.slice(0, 8).map((item, i) => (
          <span key={`${item.year}-${i}`}>
            {text(item.year)} · {item.total ?? 0} 项
            {item.avg_rating ? ` · 均分 ${item.avg_rating}` : ""}
            {item.high_rated ? ` · 高分 ${item.high_rated}` : ""}
            {item.on_hold_or_abandoned ? ` · 搁置/抛弃 ${item.on_hold_or_abandoned}` : ""}
          </span>
        ))}
      </div>
    </div>
  );
}

export function TagDriftList({ items }: { items: AnyRecord[] }) {
  if (!items.length) return null;
  const rising = items.filter((item) => item.trend === "rising").slice(0, 5);
  const receding = items.filter((item) => item.trend === "receding").slice(0, 5);
  return (
    <div>
      <div className="section-title">Tag 漂移</div>
      <div className="evidence-row tight">
        {rising.map((item, i) => (
          <Badge key={`r-${item.tag}-${i}`} tone="good">↑ {text(item.tag)} {pct(Number(item.delta || 0) * 100)}%</Badge>
        ))}
        {receding.map((item, i) => (
          <Badge key={`d-${item.tag}-${i}`} tone="warn">↓ {text(item.tag)} {pct(Math.abs(Number(item.delta || 0)) * 100)}%</Badge>
        ))}
      </div>
    </div>
  );
}

export function AffinityList({ title, items }: { title: string; items: AnyRecord[] }) {
  if (!items.length) return null;
  return (
    <div>
      <div className="section-title">{title}</div>
      <div className="compact-list">
        {items.slice(0, 6).map((item, i) => {
          const works = list(item.works).slice(0, 3).map((w) => text(w.name, "")).filter(Boolean).join(" / ");
          return (
            <span key={`${item.name}-${item.relation}-${i}`}>
              {text(item.name)}
              <small> · {text(item.relation)} · 命中 {item.count ?? 0}{works ? ` · ${works}` : ""}</small>
            </span>
          );
        })}
      </div>
    </div>
  );
}

export function DashboardOverview({ media, data }: { media: AnyRecord[]; data: AnyRecord }) {
  const biggest = [...media].sort((a, b) => Number(b.total || 0) - Number(a.total || 0))[0];
  const bestRated = [...media]
    .filter((m) => Number(m.rated || 0) >= 3 && Number.isFinite(Number(m.avg_rating)))
    .sort((a, b) => Number(b.avg_rating || 0) - Number(a.avg_rating || 0))[0];
  const drift: AnyRecord[] = media
    .flatMap((m): AnyRecord[] => list<AnyRecord>(m.tag_drift).map((item): AnyRecord => ({ ...item, subject_type: m.subject_type })))
    .sort((a, b) => Math.abs(Number(b.delta || 0)) - Math.abs(Number(a.delta || 0)))
    .slice(0, 4);
  const creators: AnyRecord[] = media
    .flatMap((m): AnyRecord[] => [
      ...list<AnyRecord>(m.studio_affinity).map((x): AnyRecord => ({ ...x, kind: "制作/开发", subject_type: m.subject_type })),
      ...list<AnyRecord>(m.staff_affinity).map((x): AnyRecord => ({ ...x, kind: "staff", subject_type: m.subject_type })),
      ...list<AnyRecord>(m.cv_affinity).map((x): AnyRecord => ({ ...x, kind: "CV", subject_type: m.subject_type })),
    ])
    .sort((a, b) => Number(b.count || 0) - Number(a.count || 0))
    .slice(0, 4);
  return (
    <div className="dashboard-overview">
      <div className="overview-card">
        <div className="metric-label">主收藏媒介</div>
        <div className="overview-value">{text(biggest?.subject_type, "-")}</div>
        <p className="card-note">{biggest ? `${biggest.total ?? 0} 项 · 已评分 ${biggest.rated ?? 0}` : "暂无收藏样本"}</p>
      </div>
      <div className="overview-card">
        <div className="metric-label">均分最高媒介</div>
        <div className="overview-value">{text(bestRated?.subject_type, "-")}</div>
        <p className="card-note">{bestRated ? `均分 ${bestRated.avg_rating} · ${bestRated.rated ?? 0} 个评分` : "评分样本不足"}</p>
      </div>
      <div className="overview-card wide">
        <div className="metric-label">近期口味漂移</div>
        {drift.length ? (
          <div className="evidence-row tight">
            {drift.map((item, i) => (
              <Badge key={`${item.subject_type}-${item.tag}-${i}`} tone={item.trend === "rising" ? "good" : "warn"}>
                {item.subject_type} {item.trend === "rising" ? "↑" : "↓"} {text(item.tag)}
              </Badge>
            ))}
          </div>
        ) : (
          <p className="card-note">年代样本不足，暂不判断 drift。</p>
        )}
      </div>
      <div className="overview-card wide">
        <div className="metric-label">高频创作者 / 声优</div>
        {creators.length ? (
          <div className="compact-list inline">
            {creators.map((item, i) => (
              <span key={`${item.kind}-${item.name}-${i}`}>
                {text(item.name)} <small>· {text(item.kind)} · {text(item.subject_type)} · {item.count ?? 0}</small>
              </span>
            ))}
          </div>
        ) : (
          <p className="card-note">尚无 enrichment 命中。</p>
        )}
      </div>
      <div className="overview-card wide">
        <div className="metric-label">下一步</div>
        <p className="card-note">{text(list<string>(data.recommendations_for_next_step)[0], "可先运行推荐或弃坑分析，把仪表盘转成行动。")}</p>
      </div>
    </div>
  );
}

export function YearSparkline({ items }: { items: AnyRecord[] }) {
  if (!items.length) return null;
  const rows = [...items]
    .sort((a, b) => String(a.year || "").localeCompare(String(b.year || "")))
    .slice(-10);
  const maxTotal = Math.max(...rows.map((x) => Number(x.total || 0)), 1);
  return (
    <div className="year-sparkline" aria-label="年度收藏趋势">
      {rows.map((item, i) => {
        const total = Number(item.total || 0);
        const high = Number(item.high_rated || 0);
        return (
          <span className="year-bar-wrap" key={`${item.year}-${i}`} title={`${item.year}: ${total} 项，高分 ${high}`}>
            <span className="year-bar" style={{ height: `${Math.max(10, Math.round((total / maxTotal) * 54))}px` }} />
            <small>{String(item.year || "").slice(2)}</small>
          </span>
        );
      })}
    </div>
  );
}

export function CollectionDashboardPanel({ data }: { data: AnyRecord }) {
  const totals = data.totals || {};
  const media = list(data.media);
  const subscriptions = data.subscriptions || {};
  const enrichment = data.enrichment || {};
  const [selectedType, setSelectedType] = useState("all");
  const visibleMedia = selectedType === "all" ? media : media.filter((m) => m.subject_type === selectedType);
  const mediaTypes = ["all", ...media.map((m) => String(m.subject_type || "")).filter(Boolean)];
  return (
    <Panel title={`收藏仪表盘 · ${text(data.username)}`} subtitle={`生成于 ${text(data.generated_at, "-")}`}>
      <div className="metric-grid">
        <div className="metric-card"><div className="metric-label">总收藏</div><div className="metric-value">{totals.items ?? 0}</div></div>
        <div className="metric-card"><div className="metric-label">已评分</div><div className="metric-value">{totals.rated ?? 0}</div></div>
        <div className="metric-card"><div className="metric-label">计划板</div><div className="metric-value">{totals.watch_plan ?? 0}</div></div>
        <div className="metric-card"><div className="metric-label">未读周报</div><div className="metric-value">{totals.unread_inbox ?? 0}</div></div>
      </div>
      {data.rating_strictness && <p className="evidence-copy">{text(data.rating_strictness)}</p>}
      <div className="evidence-row">
        {list(data.global_top_tags).slice(0, 14).map((tag) => <Badge key={tag.tag} tone="good">{text(tag.tag)} · {tag.weight}</Badge>)}
      </div>
      <DashboardOverview media={media} data={data} />
      {enrichment.enabled && (
        <div className="evidence-row">
          <Badge tone="dim">enrichment: 每类最多 {enrichment.limit_per_type ?? "-"} 条代表作</Badge>
          {Object.entries(enrichment.sampled_by_type || {}).slice(0, 5).map(([subjectType, row]) => (
            <Badge key={subjectType} tone="dim">{subjectType}: {(row as AnyRecord).sampled_count ?? 0} 样本</Badge>
          ))}
        </div>
      )}
      <div className="dashboard-filter">
        <div className="segmented" aria-label="收藏媒介筛选">
          {mediaTypes.map((kind) => (
            <button
              type="button"
              key={kind}
              className={selectedType === kind ? "active" : ""}
              onClick={() => setSelectedType(kind)}
            >
              {kind === "all" ? "全部" : kind}
            </button>
          ))}
        </div>
      </div>
      <div className="rating-grid">
        {visibleMedia.map((m, i) => (
          <div className="rating-card" key={`${m.subject_type}-${i}`}>
            <div className="rating-source">{text(m.subject_type)}</div>
            <div className="rating-score">{m.total ?? 0}</div>
            <div className="card-meta">评分 {m.rated ?? 0} · 均分 {m.avg_rating ?? "暂无"}</div>
            <div className="section-title">收藏状态</div>
            <DistributionBadges data={m.status_counts} />
            <div className="section-title">评分分布</div>
            <DistributionBadges data={m.rating_distribution} />
            <div className="section-title">年代趋势</div>
            <DistributionBadges data={m.decade_distribution} />
            <YearSparkline items={list(m.yearly_activity)} />
            <YearlyActivityList items={list(m.yearly_activity)} />
            <div className="evidence-row tight">
              {list(m.top_tags).slice(0, 7).map((tag) => <Badge key={tag.tag} tone="dim">{text(tag.tag)}</Badge>)}
            </div>
            <TagDriftList items={list(m.tag_drift)} />
            <AffinityList title="制作公司 / 开发商命中" items={list(m.studio_affinity)} />
            <AffinityList title="Staff 命中" items={list(m.staff_affinity)} />
            <AffinityList title="CV 命中" items={list(m.cv_affinity)} />
            {list<string>(m.notes).length > 0 && (
              <div className="caveats">{list<string>(m.notes).map((n, j) => <span key={j}>{n}</span>)}</div>
            )}
            <SubjectMiniList title="高分代表" items={list(m.high_rated)} />
            <SubjectMiniList title="待看/在看" items={list(m.backlog)} />
            <SubjectMiniList title="搁置/抛弃" items={list(m.on_hold_or_abandoned)} />
          </div>
        ))}
      </div>
      {!visibleMedia.length && <EmptyHint text="该媒介暂无可展示收藏数据" />}
      <div className="memory-grid">
        <div className="rating-card">
          <div className="rating-source">计划板状态</div>
          <DistributionBadges data={data.plan_summary || {}} />
        </div>
        <div className="rating-card">
          <div className="rating-source">主动订阅</div>
          <div className="evidence-row tight">
            <Badge tone={subscriptions.enabled_count ? "good" : "dim"}>
              已启用 {subscriptions.enabled_count ?? 0}
            </Badge>
            <Badge tone="dim">共 {subscriptions.total_count ?? 0} 条规则</Badge>
            {list(subscriptions.rules).slice(0, 3).map((rule) => (
              <Badge key={text(rule.id)} tone={rule.enabled ? "good" : "dim"}>
                {text(rule.title || rule.kind)}
              </Badge>
            ))}
          </div>
        </div>
      </div>
      <div className="compact-list">
        {list<string>(data.recommendations_for_next_step).map((x, i) => <span key={i}>{x}</span>)}
      </div>
      <Meta notes={list<string>(data.caveats)} />
    </Panel>
  );
}


