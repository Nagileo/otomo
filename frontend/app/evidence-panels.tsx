"use client";

import { useState, type ReactNode } from "react";

import { BirthdayPanel, ComparePanel, PilgrimagePanel, PilgrimageTripPanel, RatingMoversPanel, SubjectTrendPanel, TrendingPanel } from "./panels/media";
import { InboxPanel } from "./panels/memory";
import { PixivPanel } from "./panels/visual";

// 公共原语已 export，供 panels/ 域文件复用（新面板一律写进 panels/<域>.tsx，
// 本文件的旧面板逐步搬迁，不再新增）。

type EvidenceMap = Record<string, AnyRecord[]>;
type EvidenceMode = "user" | "dev";

import { ReviewEvidencePanel, SourceRoutingPanel, TasteAffinityPanel, WhereToWatchPanel, ReleaseFeedsPanel, BangumiIndexPanel, SeasonGuidePanel, BroadcastCalendarPanel, AiringProgressPanel, EpisodeRadarPanel, ExplorerPanel } from "./panels/media";
import { AspectProfilePanel, RecommendPanel, WatchCopilotPanel, WatchOrderPanel } from "./panels/recommend";
import { MonthlyWatchReportPanel, ProductSectionsPanel, SubjectDossierPanel, AnimeMusicThemesPanel, AnimeThemesPanel, WeeklyDigestPanel } from "./panels/product";
import { VisualTextPanel, VisualStylePanel, ImageSourcePanel, RouteImageSourcePanel, BiliVideoContentPanel, VideoFramePanel } from "./panels/visual";
import { TasteReportPanel, CollectionDashboardPanel } from "./panels/report";
import { SpoilerBadge, MemoryBadge, MemoryPanel, ClaimCheckPanel } from "./panels/memory";

import { Badge, Panel, hasActionableMemory, list, text, type AnyRecord, type MemoryState, type PrepareDownloaderHandler, type PrepareWriteHandler, type ShareSnapshotHandler, type SpoilerState } from "./panels/shared";
export { Badge, Panel, list, text } from "./panels/shared";
export type { AnyRecord } from "./panels/shared";
export { SpoilerBadge, MemoryBadge } from "./panels/memory";

export type PanelHandlers = {
  devMode?: boolean;
  onShareSnapshot?: ShareSnapshotHandler;
  onCritique?: (q: string) => void;
  onConfirmAction?: (id: string) => void;
  onCancelAction?: (id: string) => void;
  onUndoAction?: (id: string) => void;
  onPrepareWrite?: PrepareWriteHandler;
  onPrepareDownloaderPush?: PrepareDownloaderHandler;
  onVisualFeedback?: (payload: AnyRecord) => void;
  onVisualCorrectionSearch?: (query: string, subjectType?: string) => Promise<AnyRecord[]>;
};

// 展示型面板注册表：name → 中文标签。顺序即底部区默认渲染顺序；
// 也是 [[panel:name]] inline 锚定的合法名单（memory 聚合类不在此列）。
export const PANEL_LABELS: Record<string, string> = {
  route_image_source: "图片溯源",
  extract_visual_text: "图内文字",
  recommend_by_visual_style: "画风推荐",
  search_image_source: "以图搜源",
  summarize_bilibili_video_content: "视频内容",
  analyze_video_frames: "视频抽帧",
  get_pixiv_ranking: "Pixiv 榜单",
  search_pixiv_illusts: "Pixiv 检索",
  get_pixiv_artist_portfolio: "Pixiv 画师",
  get_trending_subjects: "全站热门",
  get_character_birthdays: "今日生日",
  compare_subjects: "作品对比",
  get_pilgrimage_map: "圣地巡礼",
  plan_pilgrimage_trip: "巡礼行程",
  list_weekly_digest_inbox: "收件箱",
  get_broadcast_calendar: "放送日历",
  get_airing_progress: "追番进度",
  watch_cockpit: "追番驾驶舱",
  subject_dossier: "作品档案",
  franchise_map: "IP 图谱",
  monthly_watch_report: "观看报告",
  get_subject_trend: "口碑走势",
  get_rating_movers: "口碑异动",
  anime_music_themes: "OP/ED/音乐",
  where_to_watch: "观看/购买渠道",
  get_anime_release_feeds: "离线资源",
  get_bangumi_index: "目录清单",
  recommend_subjects: "推荐候选",
  season_guide_brief: "季番导视",
  review_subject: "评价证据",
  route_subject_sources: "源路由",
  compare_user_taste: "口味同步率",
  explore_voice_network: "声优网络",
  episode_buzz_radar: "分集雷达",
  search_anime_themes: "AnimeThemes",
  plan_watch_order: "补番路线",
  plan_watch_copilot: "追番副驾",
  build_weekly_digest: "周报",
  build_collection_dashboard: "收藏仪表盘",
  build_taste_report: "口味报告",
  build_aspect_profile: "口味画像",
  claim_check: "证据校验",
};

const DEV_ONLY_PANELS = new Set(["build_aspect_profile", "claim_check"]);

const MEMORY_KEYS = [
  "get_user_memory", "remember_user_preference", "forget_user_memory",
  "record_recommendation_feedback", "prepare_bangumi_write_action", "prepare_downloader_push",
  "cancel_bangumi_write_action", "upsert_watch_plan_item", "list_watch_plan",
  "record_decision_log", "save_recommendation_list",
];

export function renderPanelByName(name: string, rows: AnyRecord[], h: PanelHandlers, anchor?: string): ReactNode | null {
  if (!rows.length) return null;
  if (DEV_ONLY_PANELS.has(name) && !h.devMode) return null;
  const render = (fn: (data: AnyRecord, i: number) => ReactNode) => <>{rows.map(fn)}</>;
  switch (name) {
    case "route_image_source":
      return render((d, i) => (
        <RouteImageSourcePanel data={d} onVisualFeedback={h.onVisualFeedback} onVisualCorrectionSearch={h.onVisualCorrectionSearch} key={`${name}-${i}`} />
      ));
    case "extract_visual_text": return render((d, i) => <VisualTextPanel data={d} key={`${name}-${i}`} />);
    case "recommend_by_visual_style": return render((d, i) => <VisualStylePanel data={d} key={`${name}-${i}`} />);
    case "search_image_source": return render((d, i) => <ImageSourcePanel data={d} key={`${name}-${i}`} />);
    case "summarize_bilibili_video_content": return render((d, i) => <BiliVideoContentPanel data={d} key={`${name}-${i}`} />);
    case "analyze_video_frames": return render((d, i) => <VideoFramePanel data={d} key={`${name}-${i}`} />);
    case "get_pixiv_ranking":
    case "search_pixiv_illusts":
    case "get_pixiv_artist_portfolio":
      return render((d, i) => <PixivPanel data={d} key={`${name}-${i}`} />);
    case "get_trending_subjects": return render((d, i) => <TrendingPanel data={d} key={`${name}-${i}`} />);
    case "get_subject_trend": return render((d, i) => <SubjectTrendPanel data={d} key={`${name}-${i}`} />);
    case "get_rating_movers": return render((d, i) => <RatingMoversPanel data={d} key={`${name}-${i}`} />);
    case "get_character_birthdays": return render((d, i) => <BirthdayPanel data={d} key={`${name}-${i}`} />);
    case "compare_subjects": return render((d, i) => <ComparePanel data={d} key={`${name}-${i}`} />);
    case "get_pilgrimage_map": return render((d, i) => <PilgrimagePanel data={d} key={`${name}-${i}`} />);
    case "plan_pilgrimage_trip": return render((d, i) => <PilgrimageTripPanel data={d} key={`${name}-${i}`} />);
    case "list_weekly_digest_inbox": return render((d, i) => <InboxPanel data={d} key={`${name}-${i}`} />);
    case "get_broadcast_calendar": return render((d, i) => <BroadcastCalendarPanel data={d} onPrepareWrite={h.onPrepareWrite} key={`${name}-${i}`} />);
    case "get_airing_progress": return render((d, i) => <AiringProgressPanel data={d} key={`${name}-${i}`} />);
    case "watch_cockpit": return render((d, i) => <ProductSectionsPanel data={d} title="追番驾驶舱" shareType="watch_cockpit" onShareSnapshot={h.onShareSnapshot} key={`${name}-${i}`} />);
    case "subject_dossier": return render((d, i) => <SubjectDossierPanel data={d} onShareSnapshot={h.onShareSnapshot} key={`${name}-${i}`} />);
    case "franchise_map": return render((d, i) => <ProductSectionsPanel data={d} title="IP 图谱" key={`${name}-${i}`} />);
    case "monthly_watch_report": return render((d, i) => <MonthlyWatchReportPanel data={d} onShareSnapshot={h.onShareSnapshot} key={`${name}-${i}`} />);
    case "anime_music_themes": return render((d, i) => <AnimeMusicThemesPanel data={d} key={`${name}-${i}`} />);
    case "where_to_watch": return render((d, i) => <WhereToWatchPanel data={d} key={`${name}-${i}`} />);
    case "get_anime_release_feeds": return render((d, i) => <ReleaseFeedsPanel data={d} onPrepareDownloaderPush={h.onPrepareDownloaderPush} key={`${name}-${i}`} />);
    case "get_bangumi_index": return render((d, i) => <BangumiIndexPanel data={d} onPrepareWrite={h.onPrepareWrite} key={`${name}-${i}`} />);
    case "recommend_subjects": return render((d, i) => <RecommendPanel data={d} onCritique={h.onCritique} onPrepareWrite={h.onPrepareWrite} key={`${name}-${i}`} />);
    case "season_guide_brief": return render((d, i) => <SeasonGuidePanel data={d} onPrepareWrite={h.onPrepareWrite} onShareSnapshot={h.onShareSnapshot} anchor={anchor} key={`${name}-${anchor || "all"}-${i}`} />);
    case "review_subject": return render((d, i) => <ReviewEvidencePanel data={d} key={`${name}-${i}`} />);
    case "route_subject_sources": return render((d, i) => <SourceRoutingPanel data={d} key={`${name}-${i}`} />);
    case "compare_user_taste": return render((d, i) => <TasteAffinityPanel data={d} key={`${name}-${i}`} />);
    case "explore_voice_network": return render((d, i) => <ExplorerPanel data={d} key={`${name}-${i}`} />);
    case "episode_buzz_radar": return render((d, i) => <EpisodeRadarPanel data={d} key={`${name}-${i}`} />);
    case "search_anime_themes": return render((d, i) => <AnimeThemesPanel data={d} key={`${name}-${i}`} />);
    case "plan_watch_order": return render((d, i) => <WatchOrderPanel data={d} onShareSnapshot={h.onShareSnapshot} key={`${name}-${i}`} />);
    case "plan_watch_copilot": return render((d, i) => <WatchCopilotPanel data={d} key={`${name}-${i}`} />);
    case "build_weekly_digest": return render((d, i) => <WeeklyDigestPanel data={d} key={`${name}-${i}`} />);
    case "build_collection_dashboard": return render((d, i) => <CollectionDashboardPanel data={d} key={`${name}-${i}`} />);
    case "build_taste_report": return render((d, i) => <TasteReportPanel data={d} key={`${name}-${i}`} />);
    case "build_aspect_profile": return render((d, i) => <AspectProfilePanel data={d} key={`${name}-${i}`} />);
    case "claim_check": return render((d, i) => <ClaimCheckPanel data={d} key={`${name}-${i}`} />);
    default:
      return null;
  }
}

/** 该 evidence 下有数据、且允许在当前模式渲染的面板名（按注册表顺序）。 */
export function availablePanelNames(evidence: EvidenceMap, devMode: boolean): string[] {
  return Object.keys(PANEL_LABELS).filter((name) => {
    if (DEV_ONLY_PANELS.has(name) && !devMode) return false;
    return list(evidence[name]).length > 0;
  });
}

export function EvidencePanels({
  evidence,
  mode = "user",
  excludeNames = [],
  collapsible = false,
  onShareSnapshot,
  onCritique,
  onConfirmAction,
  onCancelAction,
  onUndoAction,
  onPrepareWrite,
  onPrepareDownloaderPush,
  onVisualFeedback,
  onVisualCorrectionSearch,
}: {
  evidence: EvidenceMap;
  mode?: EvidenceMode;
  excludeNames?: string[];
  collapsible?: boolean;
  onCritique?: (q: string) => void;
  onConfirmAction?: (id: string) => void;
  onCancelAction?: (id: string) => void;
  onUndoAction?: (id: string) => void;
  onPrepareWrite?: PrepareWriteHandler;
  onPrepareDownloaderPush?: PrepareDownloaderHandler;
  onVisualFeedback?: (payload: AnyRecord) => void;
  onVisualCorrectionSearch?: (query: string, subjectType?: string) => Promise<AnyRecord[]>;
  onShareSnapshot?: ShareSnapshotHandler;
}) {
  const devMode = mode === "dev";
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const handlers: PanelHandlers = {
    devMode, onShareSnapshot, onCritique, onConfirmAction, onCancelAction, onUndoAction,
    onPrepareWrite, onPrepareDownloaderPush, onVisualFeedback, onVisualCorrectionSearch,
  };
  const exclude = new Set(excludeNames);
  const names = availablePanelNames(evidence, devMode).filter((n) => !exclude.has(n));
  const memoryEvidence = MEMORY_KEYS.flatMap((k) => list(evidence[k]));
  // 记忆快照是累积状态：一次回复内多次 prepare/记忆写入会各发一份完整快照，
  // 全部渲染会把同一个「长期记忆」面板叠 N 遍（待确认 1→2→3→…）；只保留最新一份。
  const latestMemory = memoryEvidence.length
    ? [memoryEvidence.reduce((a, b) => (String(b?.updated ?? "") >= String(a?.updated ?? "") ? b : a))]
    : [];
  const memory = devMode ? latestMemory : latestMemory.filter(hasActionableMemory);
  if (!names.length && !memory.length) return null;
  const memoryNode = memory.map((data, i) => (
    <MemoryPanel data={data} key={`memory-${i}`} onConfirmAction={onConfirmAction} onCancelAction={onCancelAction} onUndoAction={onUndoAction} />
  ));
  if (!collapsible) {
    return (
      <div className={`evidence-stack ${devMode ? "dev-mode" : "user-mode"}`}>
        {names.map((n) => renderPanelByName(n, list(evidence[n]), handlers))}
        {memoryNode}
      </div>
    );
  }
  // 产品级面板（报告/驾驶舱/档案/图谱/对比/巡礼行程）本身就是回答的交付物：
  // LLM 忘记输出 [[panel:]] 锚点时不能折叠成小 chip 藏起来，未锚定也自动展开。
  const AUTO_EXPAND = new Set([
    "monthly_watch_report", "watch_cockpit", "subject_dossier", "franchise_map",
    "build_taste_report", "build_collection_dashboard", "compare_subjects", "plan_pilgrimage_trip",
  ]);
  const autoOpen = names.filter((n) => AUTO_EXPAND.has(n));
  const chipNames = names.filter((n) => !AUTO_EXPAND.has(n));
  // 折叠模式：未被 inline 锚定的面板收成 chips，点开才展开（方案 A）
  return (
    <div className={`evidence-stack collapsed ${devMode ? "dev-mode" : "user-mode"}`}>
      <div className="panel-chips">
        {chipNames.map((n) => (
          <button
            key={n}
            type="button"
            className={`chip panel-chip${expanded[n] ? " active" : ""}`}
            onClick={() => setExpanded((prev) => ({ ...prev, [n]: !prev[n] }))}
          >
            {PANEL_LABELS[n] ?? n} · {list(evidence[n]).length}
          </button>
        ))}
      </div>
      {autoOpen.map((n) => (
        <div className="deliverable" key={`auto-${n}`}>
          <button
            type="button"
            className="deliverable-bar"
            onClick={() => setExpanded((prev) => ({ ...prev, [n]: prev[n] === false ? true : false }))}
          >
            <span className="deliverable-title">📊 {PANEL_LABELS[n] ?? n}</span>
            <span className="deliverable-hint">{expanded[n] === false ? "已折叠 · 点击展开 ▾" : "收起 ▴"}</span>
          </button>
          {expanded[n] !== false && renderPanelByName(n, list(evidence[n]), handlers)}
        </div>
      ))}
      {chipNames.filter((n) => expanded[n]).map((n) => renderPanelByName(n, list(evidence[n]), handlers))}
      {memoryNode}
    </div>
  );
}
