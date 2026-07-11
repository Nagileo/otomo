"use client";

// 推荐域面板：口味画像、推荐清单、观看副驾、补番顺序。

import { type AnyRecord, type ShareSnapshotHandler, type PrepareWriteHandler, list, text, pct, confidenceLabel, Badge, Panel, EmptyHint, ShareSnapshotButton } from "./shared";

export function AspectProfilePanel({ data }: { data: AnyRecord }) {
  const profile = data.profile || {};
  const likes = list(profile.likes);
  const dislikes = list(profile.dislikes);
  return (
    <Panel
      title={`Aspect 情感画像 · ${text(data.subject_type || profile.subject_type)}`}
      subtitle={`${text(data.extraction_source || profile.extraction_source, "none")} · ${data.samples_seen ?? profile.sample_count ?? 0} 条私评样本`}
    >
      <div className="memory-grid">
        <div>
          <div className="section-title">好球区</div>
          {likes.length ? (
            <div className="compact-list">
              {likes.map((item, i) => (
                <span key={`${item.aspect}-${i}`}>
                  {text(item.label || item.aspect)} · weight {pct(item.weight)} · {item.evidence_count ?? 0} 证据
                  {item.sample ? <small> · {item.sample}</small> : null}
                </span>
              ))}
            </div>
          ) : <EmptyHint text="暂无好球区" />}
        </div>
        <div>
          <div className="section-title">雷区</div>
          {dislikes.length ? (
            <div className="compact-list">
              {dislikes.map((item, i) => (
                <span key={`${item.aspect}-${i}`}>
                  {text(item.label || item.aspect)} · weight {pct(item.weight)} · {item.evidence_count ?? 0} 证据
                  {item.sample ? <small> · {item.sample}</small> : null}
                </span>
              ))}
            </div>
          ) : <EmptyHint text="暂无雷区" />}
        </div>
      </div>
      {list<string>(data.caveats).length > 0 && (
        <div className="caveats">{list<string>(data.caveats).map((c, i) => <span key={i}>{c}</span>)}</div>
      )}
    </Panel>
  );
}

export function RecommendPanel({
  data,
  onCritique,
  onPrepareWrite,
}: {
  data: AnyRecord;
  onCritique?: (q: string) => void;
  onPrepareWrite?: PrepareWriteHandler;
}) {
  const items = list(data.items);
  const aspectProfile = data.aspect_profile_summary || {};
  const mediaStrategy = data.media_strategy || {};
  return (
    <Panel
      title={`推荐证据 · ${text(data.subject_type)}`}
      subtitle={`scenario: ${text(data.scenario, "general")} · mode: ${text(data.mode, "normal")} · ${items.length} 个候选`}
    >
      <div className="evidence-row">
        <Badge tone="good">{text(data.scenario, "general")}</Badge>
        {list<string>(data.based_on_tags).slice(0, 10).map((tag) => <Badge key={tag} tone="dim">{tag}</Badge>)}
        {list<string>(data.applied_constraints).map((x) => <Badge key={x} tone="warn">{x}</Badge>)}
        {mediaStrategy.book_subtype && mediaStrategy.book_subtype !== "auto" && (
          <Badge tone="good">book: {text(mediaStrategy.book_subtype)}</Badge>
        )}
        {mediaStrategy.music_subtype && mediaStrategy.music_subtype !== "auto" && (
          <Badge tone="good">music: {text(mediaStrategy.music_subtype)}</Badge>
        )}
      </div>
      {mediaStrategy.policy && <p className="evidence-copy">{text(mediaStrategy.policy)}</p>}
      {(list(aspectProfile.likes).length > 0 || list(aspectProfile.dislikes).length > 0) && (
        <div className="evidence-row">
          {list(aspectProfile.likes).slice(0, 4).map((x) => <Badge key={`like-${x.aspect}`} tone="good">好球 {text(x.label || x.aspect)}</Badge>)}
          {list(aspectProfile.dislikes).slice(0, 4).map((x) => <Badge key={`dislike-${x.aspect}`} tone="warn">雷区 {text(x.label || x.aspect)}</Badge>)}
        </div>
      )}
      <div className="rec-grid">
        {items.map((item, i) => (
          <a className="rec-card" href={`https://bgm.tv/subject/${item.id}`} target="_blank" rel="noreferrer" key={`${item.id}-${i}`}>
            {item.image ? <img src={item.image} alt="" /> : <div className="rec-noimg" />}
            <div className="rec-body">
              <div className="card-title">{text(item.name)}</div>
              <div className="card-meta">
                Otomo {item.score ?? "-"} · Bangumi {item.bangumi_score ?? "暂无"}
                {item.rank ? ` · rank ${item.rank}` : ""}
              </div>
              {item.review_consensus && <p className="card-note">{item.review_consensus}</p>}
              <div className="evidence-row tight">
                {item.media_subtype && <Badge tone="dim">{text(item.media_subtype)}</Badge>}
                {list<string>(item.explicit_tag_matches).map((tag) => <Badge key={tag} tone="good">{tag}</Badge>)}
                {list<string>(item.quality_badges).map((tag) => <Badge key={tag} tone="warn">{tag}</Badge>)}
                {list<string>(item.aspect_matches).map((tag) => <Badge key={tag} tone="good">{tag}</Badge>)}
                {list<string>(item.aspect_warnings).map((tag) => <Badge key={tag} tone="warn">{tag}</Badge>)}
                {item.id && onPrepareWrite && (
                  <button
                    type="button"
                    className="inline-action card-action"
                    onClick={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      onPrepareWrite(Number(item.id), text(item.name), 1);
                    }}
                  >
                    想看
                  </button>
                )}
              </div>
              {list<string>(item.media_notes).length > 0 && (
                <div className="compact-list inline">
                  {list<string>(item.media_notes).slice(0, 3).map((r, idx) => <span key={idx}>{r}</span>)}
                </div>
              )}
              {(list<string>(item.why_recalled).length > 0 || list<string>(item.fit_points).length > 0 || list<string>(item.risks).length > 0) && (
                <div className="taste-groups mini">
                  <div>
                    <div className="section-title">召回</div>
                    <div className="compact-list">{list<string>(item.why_recalled).slice(0, 3).map((r, idx) => <span key={idx}>{r}</span>)}</div>
                  </div>
                  <div>
                    <div className="section-title">适合</div>
                    <div className="compact-list">{list<string>(item.fit_points).slice(0, 3).map((r, idx) => <span key={idx}>{r}</span>)}</div>
                  </div>
                  {list<string>(item.risks).length > 0 && (
                    <div>
                      <div className="section-title">风险</div>
                      <div className="compact-list">{list<string>(item.risks).slice(0, 3).map((r, idx) => <span key={idx}>{r}</span>)}</div>
                    </div>
                  )}
                </div>
              )}
              {list<string>(item.next_step).length > 0 && (
                <div className="compact-list inline next-step">
                  {list<string>(item.next_step).slice(0, 3).map((r, idx) => <span key={idx}>{r}</span>)}
                </div>
              )}
              <div className="compact-list inline">
                {list<string>(item.reasons).slice(0, 4).map((r, idx) => <span key={idx}>{r}</span>)}
              </div>
              {list(item.external_mappings).length > 0 && (
                <div className="mapping-list">
                  {list(item.external_mappings).map((m, idx) => (
                    <span key={idx}>
                      {text(m.source)} 对齐《{text(m.external_title)}》 · 置信度 {confidenceLabel(m.mapping_confidence)}
                    </span>
                  ))}
                </div>
              )}
              {list<string>(item.source_routes).length > 0 && (
                <div className="compact-list">
                  {list<string>(item.source_routes).slice(0, 3).map((r, idx) => <span key={idx}>{r}</span>)}
                </div>
              )}
            </div>
          </a>
        ))}
      </div>
      {onCritique && list<string>(data.critique_chips).length > 0 && (
        <div className="followups">
          {list<string>(data.critique_chips).map((q, i) => (
            <button className="chip" key={i} onClick={() => onCritique(q)}>
              {q}
            </button>
          ))}
        </div>
      )}
      {onCritique && list<string>(data.cold_start_questions).length > 0 && (
        <div className="followups">
          {list<string>(data.cold_start_questions).map((q, i) => (
            <button className="chip" key={i} onClick={() => onCritique(q)}>
              {q}
            </button>
          ))}
        </div>
      )}
      {list<string>(data.mapping_warnings).length > 0 && (
        <div className="caveats">
          <div className="section-title">映射告警（未安全对齐，已跳过）</div>
          {list<string>(data.mapping_warnings).map((w, i) => <span key={i}>⚠ {w}</span>)}
        </div>
      )}
      {data.feedback_policy && (
        <div className="caveats">
          <span>
            反馈闭环：正向 {data.feedback_policy.positive ?? 0} / 负向 {data.feedback_policy.negative ?? 0}
            {list<string>(data.feedback_policy.positive_tags).length ? ` · 正向标签 ${list<string>(data.feedback_policy.positive_tags).slice(0, 4).join("、")}` : ""}
            {list<string>(data.feedback_policy.negative_tags).length ? ` · 避雷标签 ${list<string>(data.feedback_policy.negative_tags).slice(0, 4).join("、")}` : ""}
          </span>
        </div>
      )}
      {list<string>(data.notes).length > 0 && (
        <div className="caveats">{list<string>(data.notes).map((n, i) => <span key={i}>{n}</span>)}</div>
      )}
    </Panel>
  );
}

export function WatchCopilotPanel({ data }: { data: AnyRecord }) {
  const queue = list(data.queue);
  const groups = [
    ["继续追", "continue_watching"],
    ["想看开坑", "start_from_wishlist"],
    ["搁置盘活", "revive_on_hold"],
  ];
  return (
    <Panel title={`追番副驾 · ${text(data.username)}`} subtitle={`${queue.length} 个本周候选`}>
      <div className="evidence-row">
        {list<string>(data.profile_tags).slice(0, 10).map((tag) => <Badge key={tag} tone="dim">{tag}</Badge>)}
      </div>
      <div className="rec-grid">
        {queue.map((item, i) => (
          <a className="rec-card" href={`https://bgm.tv/subject/${item.id}`} target="_blank" rel="noreferrer" key={`${item.id}-${i}`}>
            {item.image ? <img src={item.image} alt="" /> : <div className="rec-noimg" />}
            <div className="rec-body">
              <div className="card-title">{text(item.name)}</div>
              <div className="card-meta">
                {text(item.status)} · Otomo {item.score ?? "-"} · BGM {item.bangumi_score ?? "暂无"}
                {item.eps ? ` · ${item.ep_status ?? 0}/${item.eps}` : ""}
              </div>
              <Badge tone={item.status === "在看" ? "good" : item.status === "搁置" ? "warn" : "dim"}>{text(item.action)}</Badge>
              <div className="compact-list inline">
                {list<string>(item.why).slice(0, 4).map((r, idx) => <span key={idx}>{r}</span>)}
              </div>
            </div>
          </a>
        ))}
      </div>
      <div className="taste-groups">
        {groups.map(([label, key]) => (
          <div className="taste-group" key={key}>
            <div className="section-title">{label}</div>
            {list(data[key]).length ? (
              <div className="compact-list">
                {list(data[key]).slice(0, 5).map((item, i) => (
                  <span key={`${item.id}-${i}`}>{text(item.name)} · {text(item.action)}</span>
                ))}
              </div>
            ) : <EmptyHint text="暂无候选" />}
          </div>
        ))}
      </div>
      {list<string>(data.notes).length > 0 && (
        <div className="caveats">{list<string>(data.notes).map((n, i) => <span key={i}>{n}</span>)}</div>
      )}
    </Panel>
  );
}

export function WatchOrderPanel({ data, onShareSnapshot }: { data: AnyRecord; onShareSnapshot?: ShareSnapshotHandler }) {
  const main = list(data.watch_order);
  const sides = list(data.side_stories);
  const alternates = list(data.alternate_routes);
  const skips = list(data.skip_candidates);
  const tone = (necessity: any) => {
    const n = String(necessity || "");
    if (n === "required") return "good";
    if (n === "optional" || n === "skip") return "warn";
    return "dim";
  };
  const label = (necessity: any) => {
    const n = String(necessity || "");
    if (n === "required") return "必看";
    if (n === "optional") return "可选";
    if (n === "skip") return "可跳过";
    return "建议";
  };
  const renderItems = (items: AnyRecord[], compact = false) => (
    <div className={compact ? "watch-order-list compact" : "watch-order-list"}>
      {items.map((item, i) => (
        <a className="watch-order-item" href={`https://bgm.tv/subject/${item.id}`} target="_blank" rel="noreferrer" key={`${item.id}-${i}`}>
          <div className="watch-order-index">{item.order ?? i + 1}</div>
          <div className="watch-order-body">
            <div className="watch-order-top">
              <span className="card-title">{text(item.name)}</span>
              <Badge tone={tone(item.necessity)}>{label(item.necessity)}</Badge>
            </div>
            <div className="card-meta">
              {text(item.relation || item.watch_role, "主线")}
              {item.date ? ` · ${item.date}` : ""}
              {item.duration_hint ? ` · ${item.duration_hint}` : ""}
              {item.score ? ` · BGM ${item.score}` : ""}
            </div>
            {item.skip_advice ? <p className="card-note">{text(item.skip_advice)}</p> : null}
          </div>
        </a>
      ))}
    </div>
  );
  return (
    <Panel title={`补番路线 · ${text(data.ip)}`} subtitle="按 Bangumi 关系边、播出日期和必要性整理">
      <div className="panel-actions">
        <ShareSnapshotButton
          type="watch_order"
          title={`补番路线 · ${text(data.ip)}`}
          payload={data}
          onShareSnapshot={onShareSnapshot}
        />
      </div>
      <div className="evidence-row">
        <Badge tone="good">主线 {main.length}</Badge>
        <Badge tone="dim">旁支 {sides.length}</Badge>
        <Badge tone="dim">不同演绎 {alternates.length}</Badge>
        <Badge tone={skips.length ? "warn" : "good"}>可跳过 {skips.length}</Badge>
      </div>
      {main.length > 0 ? (
        <>
          <div className="section-title">主线顺序</div>
          {renderItems(main)}
        </>
      ) : <EmptyHint text="没有主线条目" />}
      {sides.length > 0 && (
        <>
          <div className="section-title">旁支 / OVA / 番外</div>
          {renderItems(sides, true)}
        </>
      )}
      {alternates.length > 0 && (
        <>
          <div className="section-title">不同演绎 / 重制 / 替代路线</div>
          {renderItems(alternates, true)}
        </>
      )}
      {skips.length > 0 && (
        <>
          <div className="section-title">可跳过候选</div>
          <div className="compact-list">
            {skips.map((item, i) => (
              <span key={`${item.id}-${i}`}>{text(item.name)} · {text(item.skip_advice)}</span>
            ))}
          </div>
        </>
      )}
      {list<string>(data.notes).length > 0 && (
        <div className="caveats">{list<string>(data.notes).map((n, i) => <span key={i}>{n}</span>)}</div>
      )}
    </Panel>
  );
}


