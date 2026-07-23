"use client";

// 记忆/计划域面板：长期记忆、计划板、决策与通知类证据。
// 既有面板（Memory/WatchPlan/写回确认流相关）后续搬迁至此；新记忆域面板一律写在本文件。

import { Badge, Panel, list, text, type AnyRecord , Meta } from "./shared";
import { type SpoilerState, type MemoryState, pct, sourceTone, EmptyHint } from "./shared";

const KIND_LABEL: Record<string, string> = {
  weekly_digest: "周报",
  daily_airing: "每日追番",
  system: "系统",
};

export function InboxPanel({ data }: { data: AnyRecord }) {
  const items = list(data.items);
  return (
    <Panel title={`收件箱 · ${text(data.username)}`} subtitle={`${items.length} 条通知`}>
      {items.length === 0 && <div className="empty-hint">收件箱是空的；订阅周报或每日追番提醒后会出现在这里。</div>}
      <div className="inbox-list">
        {items.map((it, i) => {
          const payload = (it.payload || {}) as AnyRecord;
          const sections = list(payload.sections);
          return (
            <div key={i} className={`inbox-item${it.unread ? " unread" : ""}`}>
              <div className="inbox-head">
                <Badge tone={it.unread ? "warn" : "dim"}>{KIND_LABEL[text(it.kind, "system")] ?? text(it.kind)}</Badge>
                <span className="inbox-title">{text(it.title, "通知")}</span>
                <span className="inbox-time">{text(it.created_at, "")}</span>
              </div>
              {sections.slice(0, 2).map((sec, j) => (
                <div key={j} className="inbox-section">
                  <div className="inbox-section-title">{text(sec.title, "")}</div>
                  <ul>
                    {list(sec.items)
                      .slice(0, 4)
                      .map((row, k) => (
                        <li key={k}>
                          {text(row.name || row.title, "条目")}
                          {row.behind ? <Badge tone="warn">落后 {row.behind} 集</Badge> : null}
                          {row.subgroup ? <Badge tone="dim">{text(row.subgroup)}</Badge> : null}
                        </li>
                      ))}
                  </ul>
                </div>
              ))}
            </div>
          );
        })}
      </div>
    </Panel>
  );
}

export function SpoilerBadge({ spoiler }: { spoiler: SpoilerState | null }) {
  if (!spoiler) return null;
  const mode = spoiler.mode || "none";
  const tone = mode === "full" ? "bad" : mode === "mild" ? "warn" : "good";
  return (
    <div className="spoiler-state">
      <Badge tone={tone}>剧透: {mode}</Badge>
      {spoiler.memory_default && spoiler.memory_default !== mode && (
        <Badge tone="dim">长期默认 {spoiler.memory_default}</Badge>
      )}
      {spoiler.soft_warning && <Badge tone="warn">先标注剧透</Badge>}
      {spoiler.progress_episode !== undefined && spoiler.progress_episode !== null && (
        <Badge tone="dim">进度: 第 {spoiler.progress_episode} 集</Badge>
      )}
      {spoiler.pending_followup && <Badge tone="warn">等待确认</Badge>}
    </div>
  );
}

export function MemoryBadge({ memory }: { memory: MemoryState | null }) {
  if (!memory) return null;
  const likeCount = list(memory.likes).length;
  const dislikeCount = list(memory.dislikes).length;
  const feedbackCount = list(memory.recent_feedback).length;
  const visualFeedbackCount = list(memory.recent_visual_feedback).length;
  const pendingCount = list(memory.pending_write_actions).length;
  const planCount = list(memory.watch_plan).length;
  const inboxCount = list(memory.inbox).filter((x) => x.unread).length;
  const likePreview = list(memory.likes).slice(0, 3).map((x) => text(x.value, "")).filter(Boolean).join(" / ");
  const dislikePreview = list(memory.dislikes).slice(0, 3).map((x) => text(x.value, "")).filter(Boolean).join(" / ");
  return (
    <div className="memory-state">
      <Badge tone="dim">记忆: {text(memory.username, "未绑定")}</Badge>
      <Badge tone={likeCount ? "good" : "dim"}>喜欢 {likeCount}{likePreview ? ` · ${likePreview}` : ""}</Badge>
      <Badge tone={dislikeCount ? "warn" : "dim"}>避雷 {dislikeCount}{dislikePreview ? ` · ${dislikePreview}` : ""}</Badge>
      {feedbackCount > 0 && <Badge tone="dim">反馈 {feedbackCount}</Badge>}
      {visualFeedbackCount > 0 && <Badge tone="dim">视觉纠错 {visualFeedbackCount}</Badge>}
      {pendingCount > 0 && <Badge tone="warn">待确认 {pendingCount}</Badge>}
      {planCount > 0 && <Badge tone="good">计划 {planCount}</Badge>}
      {inboxCount > 0 && <Badge tone="warn">未读周报 {inboxCount}</Badge>}
      {memory.spoiler_default && memory.spoiler_default !== "none" && (
        <Badge tone={memory.spoiler_default === "full" ? "bad" : "warn"}>默认剧透 {memory.spoiler_default}</Badge>
      )}
    </div>
  );
}

export function MemoryPanel({
  data,
  onConfirmAction,
  onCancelAction,
  onUndoAction,
}: {
  data: MemoryState;
  onConfirmAction?: (id: string) => void;
  onCancelAction?: (id: string) => void;
  onUndoAction?: (id: string) => void;
}) {
  const likes = list(data.likes);
  const dislikes = list(data.dislikes);
  const feedback = list(data.recent_feedback);
  const visualFeedback = list(data.recent_visual_feedback);
  const pendingActions = list(data.pending_write_actions);
  const decisions = list(data.recent_decisions);
  const watchPlan = list(data.watch_plan);
  const recLists = list(data.recommendation_lists);
  const inbox = list(data.inbox);
  const progress = data.progress || {};
  const progressEntries = Object.entries(progress).slice(0, 12);
  const profiles = Object.entries(data.profile_snapshot || {}).slice(0, 3);
  const aspectProfiles = Object.entries(data.aspect_profiles || {}).slice(0, 4);
  return (
    <Panel
      title={`长期记忆 · ${text(data.username, "unknown")}`}
      subtitle={`喜欢 ${likes.length} · 避雷 ${dislikes.length} · 反馈 ${feedback.length} · 视觉纠错 ${visualFeedback.length}`}
    >
      <div className="evidence-row">
        <Badge tone="dim">spoiler_default: {text(data.spoiler_default, "none")}</Badge>
        {data.updated_at && <Badge tone="dim">updated {data.updated_at}</Badge>}
        {pendingActions.length > 0 && <Badge tone="warn">待确认写回 {pendingActions.length}</Badge>}
        {watchPlan.length > 0 && <Badge tone="good">计划板 {watchPlan.length}</Badge>}
        {inbox.filter((x) => x.unread).length > 0 && <Badge tone="warn">未读 inbox {inbox.filter((x) => x.unread).length}</Badge>}
      </div>

      {pendingActions.length > 0 && (
        <>
          <div className="section-title">待确认动作</div>
          <div className="action-list">
            {pendingActions.map((action, i) => (
              <div className="action-card" key={`${action.id}-${i}`}>
                <div>
                  <div className="card-title">{text(action.summary)}</div>
                  <div className="card-meta">
                    {text(action.operation)} · {text(action.subject_name || action.subject_id, "未知条目")}
                  </div>
                  <div className="card-meta">
                    {action.operation === "push_downloader" ? "等待你确认后才会推送到下载器" : "等待你确认后才会写回 Bangumi"}
                  </div>
                </div>
                <div className="action-buttons">
                  {onConfirmAction && (
                    <button className="chip action-confirm" onClick={() => onConfirmAction(text(action.id, ""))}>
                      {action.operation === "push_downloader" ? "确认推送" : "确认写回"}
                    </button>
                  )}
                  {onCancelAction && <button className="chip" onClick={() => onCancelAction(text(action.id, ""))}>取消</button>}
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      <div className="memory-grid">
        <div>
          <div className="section-title">喜欢 / 正偏好</div>
          {likes.length ? (
            <div className="compact-list">
              {likes.map((item, i) => (
                <span key={`${item.value}-${i}`}>
                  {text(item.value)}
                  <small> · {text(item.source, "unknown")} · {pct(item.confidence)}</small>
                </span>
              ))}
            </div>
          ) : (
            <EmptyHint text="还没有长期喜欢项" />
          )}
        </div>
        <div>
          <div className="section-title">避雷 / 负偏好</div>
          {dislikes.length ? (
            <div className="compact-list">
              {dislikes.map((item, i) => (
                <span key={`${item.value}-${i}`}>
                  {text(item.value)}
                  <small> · {text(item.source, "unknown")} · {pct(item.confidence)}</small>
                </span>
              ))}
            </div>
          ) : (
            <EmptyHint text="还没有长期避雷项" />
          )}
        </div>
      </div>

      {progressEntries.length > 0 && (
        <>
          <div className="section-title">观看进度</div>
          <div className="evidence-row">
            {progressEntries.map(([subject, item]) => (
              <Badge key={subject} tone={sourceTone(item.source)}>
                {subject}: 第 {item.episode ?? "-"} 集
              </Badge>
            ))}
          </div>
        </>
      )}

      {visualFeedback.length > 0 && (
        <>
          <div className="section-title">视觉识别反馈</div>
          <div className="compact-list">
            {visualFeedback.slice(-8).map((item, i) => (
              <span key={`${item.id}-${i}`}>
                {text(item.predicted_subject_name || item.predicted_title, "候选")} · {text(item.signal)}
                <small> · {pct(item.confidence)}{item.note ? ` · ${text(item.note)}` : ""}</small>
              </span>
            ))}
          </div>
        </>
      )}

      {profiles.length > 0 && (
        <>
          <div className="section-title">画像摘要</div>
          <div className="memory-grid">
            {profiles.map(([subjectType, profile]) => (
              <div className="rating-card" key={subjectType}>
                <div className="rating-source">{subjectType}</div>
                <div className="card-meta">
                  看过 {profile.watched ?? "-"} · 均分 {profile.avg_rating ?? "暂无"}
                </div>
                <div className="evidence-row tight">
                  {list(profile.top_tags).slice(0, 6).map((item) => (
                    <Badge key={item.tag} tone="dim">{text(item.tag)}</Badge>
                  ))}
                </div>
                {list<string>(profile.favorites).length > 0 && (
                  <div className="compact-list inline">
                    {list<string>(profile.favorites).slice(0, 3).map((name) => <span key={name}>{name}</span>)}
                  </div>
                )}
              </div>
            ))}
          </div>
        </>
      )}

      {aspectProfiles.length > 0 && (
        <>
          <div className="section-title">Aspect 好球区 / 雷区</div>
          <div className="memory-grid">
            {aspectProfiles.map(([subjectType, profile]) => (
              <div className="rating-card" key={subjectType}>
                <div className="rating-source">{subjectType}</div>
                <div className="card-meta">
                  {text(profile.extraction_source, "none")} · {profile.sample_count ?? 0} 样本
                </div>
                <div className="evidence-row tight">
                  {list(profile.likes).slice(0, 4).map((item) => (
                    <Badge key={`like-${item.aspect}`} tone="good">好球 {text(item.label || item.aspect)}</Badge>
                  ))}
                  {list(profile.dislikes).slice(0, 4).map((item) => (
                    <Badge key={`dislike-${item.aspect}`} tone="warn">雷区 {text(item.label || item.aspect)}</Badge>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      {feedback.length > 0 && (
        <>
          <div className="section-title">近期推荐反馈</div>
          <div className="compact-list">
            {feedback.map((item, i) => (
              <span key={`${item.name || item.subject_id}-${i}`}>
                {text(item.name || item.subject_id, "候选")} · {text(item.signal)}
                {item.note ? ` · ${item.note}` : ""}
              </span>
            ))}
          </div>
        </>
      )}

      {watchPlan.length > 0 && (
        <>
          <div className="section-title">计划板</div>
          <div className="rating-grid">
            {watchPlan.slice(0, 8).map((item, i) => (
              <div className="rating-card" key={`${item.subject_id}-${i}`}>
                <div className="rating-source">{text(item.status)} · priority {item.priority ?? "-"}</div>
                <div className="card-title">{text(item.name || item.subject_id)}</div>
                <p className="card-note">{text(item.reason, "")}</p>
                <div className="evidence-row tight">
                  {list<string>(item.tags).slice(0, 5).map((tag) => <Badge key={tag} tone="dim">{tag}</Badge>)}
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      {inbox.length > 0 && (
        <>
          <div className="section-title">订阅 Inbox</div>
          <div className="rating-grid">
            {inbox.slice().reverse().slice(0, 5).map((item, i) => {
              const payload = item.payload || {};
              return (
                <div className="rating-card" key={`${item.id}-${i}`}>
                  <div className="rating-source">{item.unread ? "未读" : "已读"} · {text(item.created_at, "")}</div>
                  <div className="card-title">{text(item.title)}</div>
                  <p className="card-note">
                    {text(payload.week, "")}
                    {list(payload.sections).length ? ` · ${list(payload.sections).map((s) => text(s.title)).join(" / ")}` : ""}
                  </p>
                </div>
              );
            })}
          </div>
        </>
      )}

      {decisions.length > 0 && (
        <>
          <div className="section-title">近期决策</div>
          <div className="compact-list">
            {decisions.slice().reverse().slice(0, 8).map((item, i) => (
              <span key={`${item.id}-${i}`}>
                {text(item.kind)} · {text(item.subject_name || item.subject_id, "条目")} · {text(item.reason, "")}
                {item.kind === "write" && onUndoAction && item.action_id ? (
                  <button className="inline-action" onClick={() => onUndoAction(text(item.action_id, ""))}>撤销</button>
                ) : null}
              </span>
            ))}
          </div>
        </>
      )}

      {recLists.length > 0 && (
        <>
          <div className="section-title">保存的推荐列表</div>
          <div className="compact-list">
            {recLists.slice().reverse().slice(0, 4).map((item, i) => (
              <span key={`${item.id}-${i}`}>
                {text(item.title)} · {text(item.subject_type)} · {list(item.items).length} 项
                {item.reason ? ` · ${item.reason}` : ""}
              </span>
            ))}
          </div>
        </>
      )}
    </Panel>
  );
}


export function ClaimCheckPanel({ data }: { data: AnyRecord }) {
  const claims = list(data.claims);
  const verifiableCount = Number(data.supported_count || 0) + Number(data.unsupported_count || 0);
  const supportLabel = verifiableCount ? `${(Number(data.support_rate || 0) * 100).toFixed(0)}%` : "N/A";
  return (
    <Panel
      title="逐条事实校验"
      subtitle={
        verifiableCount
          ? `support ${supportLabel} · supported ${data.supported_count ?? 0} · unsupported ${data.unsupported_count ?? 0}`
          : "本轮没有强 canonical 硬事实需要自动回退"
      }
    >
      <div className="metric-grid">
        <div className="metric-card">
          <div className="metric-label">支持率</div>
          <div className="metric-value">{supportLabel}</div>
        </div>
        <div className="metric-card">
          <div className="metric-label">未支持</div>
          <div className="metric-value">{data.unsupported_count ?? 0}</div>
        </div>
        <div className="metric-card">
          <div className="metric-label">不可验证</div>
          <div className="metric-value">{data.unverifiable_count ?? 0}</div>
        </div>
        <div className="metric-card">
          <div className="metric-label">需修正</div>
          <div className="metric-value">{data.needs_revision ? "是" : "否"}</div>
        </div>
      </div>
      {list<string>(data.revision_hints).length > 0 && (
        <>
          <div className="section-title">修正建议</div>
          <div className="compact-list">
            {list<string>(data.revision_hints).map((hint, i) => <span key={i}>{hint}</span>)}
          </div>
        </>
      )}
      {claims.length ? (
        <div className="claim-list">
          {claims.slice(0, 12).map((claim, i) => {
            const tone = claim.supported ? "good" : claim.severity === "block" ? "bad" : claim.severity === "warn" ? "warn" : "dim";
            return (
              <div className="claim-card" key={`${claim.text}-${i}`}>
                <div className="claim-top">
                  <Badge tone={tone}>{claim.supported ? "supported" : "unsupported"}</Badge>
                  <Badge tone="dim">{text(claim.kind)}</Badge>
                  {claim.severity && <Badge tone={tone}>{text(claim.severity)}</Badge>}
                  <Badge tone="dim">conf {pct(claim.confidence)}</Badge>
                </div>
                <p className="card-note">{text(claim.text)}</p>
                {list(claim.evidence).length > 0 ? (
                  <div className="compact-list inline">
                    {list(claim.evidence).slice(0, 3).map((ev, idx) => (
                      <span key={idx}>{text(ev.source)} · {text(ev.text, "")}</span>
                    ))}
                  </div>
                ) : (
                  <div className="card-meta">{text(claim.note, "没有命中本轮证据")}</div>
                )}
                {claim.suggestion && <div className="card-meta">建议：{text(claim.suggestion)}</div>}
              </div>
            );
          })}
        </div>
      ) : (
        <EmptyHint text="最终答案没有切出可校验 claim" />
      )}
      <Meta notes={list<string>(data.caveats)} />
    </Panel>
  );
}


