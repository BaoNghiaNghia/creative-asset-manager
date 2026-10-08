import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { BrandIcon } from "../components/Icons";
import { WorkspaceNavigation } from "../components/WorkspaceNavigation";
import { WorkspaceBackToAssets, WorkspacePageHeader } from "../components/WorkspacePageHeader";
import {
  analyzeStage3ReviewGroups,
  cancelStage2Jobs,
  createStage2Job,
  listKeywordAnalysis,
  listSourcePlans,
  listStage2Jobs,
  listStage3ReviewGroups,
  markCandidateReferenceFeedback,
  setKeywordAnalysisPicked,
  setKeywordAnalysisFavorite,
  setKeywordScoutFeedback,
  type ScoutFeedbackAction,
  type ScoutFeedbackScope,
  syncSourcePlans,
  type SourcePlanSortBy,
  type SourcePlanSortDirection,
  type KeywordAnalysisSortBy,
  type KeywordAnalysisSortDirection,
  type KeywordUsageFilter,
  type KeywordTailFilter,
} from "./api";
import { RrugcWorkflowSettingsModal } from "./RrugcWorkflowSettingsModal";
import { SkillManagerModal } from "./SkillManagerModal";
import { KeywordAnalysisTable } from "./KeywordAnalysisTable";
import { EmbroideryColorwayStage } from "./EmbroideryColorwayStage";
import { SourcePlanTable } from "./SourcePlanTable";
import { Stage2JobTable } from "./Stage2JobTable";
import { Stage3ReviewGroups } from "./Stage3ReviewGroups";
import type { KeywordVolumePage, ReferenceManualLabel, SourcePlan, SourcePlanPage, SourcePlanReferencePreview, Stage2Job, Stage2SkillSelection, Stage3ReviewGroupList } from "./types";
import "./ui-overhaul.css";
import "./tablet-mobile-density.css";

const EMPTY_KEYWORD_PAGE: KeywordVolumePage = {
  items: [],
  page: 1,
  page_size: 20,
  total: 0,
  overview: {
    total_keywords: 0,
    total_search_volume: 0,
    average_search_volume: 0,
    average_cpc: null,
    high_competition: 0,
    zero_volume: 0,
    short_tail_keywords: 0,
    mid_tail_keywords: 0,
    long_tail_keywords: 0,
    picked_keywords: 0,
    favorite_keywords: 0,
  },
};

export const STAGE2_REFERENCES_PER_RUN = 3;

export function stage2ReferenceBatches(candidateIds: string[]) {
  const batches: string[][] = [];
  for (let index = 0; index < candidateIds.length; index += STAGE2_REFERENCES_PER_RUN) {
    batches.push(candidateIds.slice(index, index + STAGE2_REFERENCES_PER_RUN));
  }
  return batches;
}

const EMPTY_STAGE3_GROUPS: Stage3ReviewGroupList = {
  items: [],
  total_groups: 0,
  total_images: 0,
  ready_images: 0,
  rejected_images: 0,
  analyzing_images: 0,
  pending_images: 0,
  error_images: 0,
};

const EMPTY_SOURCE_PAGE: SourcePlanPage = {
  items: [],
  page: 1,
  page_size: 20,
  total: 0,
  overview: {
    embroidery_groups: 0,
    source_images: 0,
    working_groups: 0,
    refs_loaded: 0,
    stage2_groups: 0,
    stage2_source_images: 0,
    stage2_drive_ready_refs: 0,
    stage2_active_jobs: 0,
  },
};

type RrugcStageTab = "stage0" | "stage1" | "stage2" | "stage3" | "stage4";

const RRUGC_STAGE_TABS: Array<{ id: RrugcStageTab; label: string; description: string; marker: string }> = [
  { id: "stage0", label: "Stage 0", description: "Analysis Keyword", marker: "0" },
  { id: "stage1", label: "Stage 1", description: "Embroidery → 13 Colors", marker: "1" },
  { id: "stage2", label: "Stage 2", description: "Pinterest References", marker: "2" },
  { id: "stage3", label: "Stage 3", description: "Image Generation", marker: "3" },
  { id: "stage4", label: "Stage 4", description: "UGC Review", marker: "4" },
];

export function sourcePlanPageRenderFingerprint(page: SourcePlanPage): string {
  return JSON.stringify({
    page: page.page,
    page_size: page.page_size,
    total: page.total,
    overview: page.overview,
    items: page.items.map(plan => ({
      ...plan,
      // Scheduler timestamps can change without affecting anything visible in
      // the Stage 1 / Stage 2 tables. Excluding them prevents a full table
      // reconciliation every poll.
      updated_at: undefined,
      scan_next_at: undefined,
      scan_last_completed_at: undefined,
    })),
  });
}

export function stage2JobsRenderFingerprint(jobs: Stage2Job[]): string {
  return JSON.stringify(jobs.map(job => ({
    ...job,
    // updated_at may advance while a worker heartbeat/progress record is
    // persisted without changing anything rendered in Stage 2.
    updated_at: undefined,
  })));
}

export function RealisticReviewUgcPage() {
  const [keywordPage, setKeywordPage] = useState<KeywordVolumePage>(EMPTY_KEYWORD_PAGE);
  const [keywordPageNumber, setKeywordPageNumber] = useState(1);
  const [keywordPageSize, setKeywordPageSize] = useState(20);
  const [keywordQuery, setKeywordQuery] = useState("");
  const [debouncedKeywordQuery, setDebouncedKeywordQuery] = useState("");
  const [keywordSortBy, setKeywordSortBy] = useState<KeywordAnalysisSortBy>("search_volume");
  const [keywordSortDirection, setKeywordSortDirection] = useState<KeywordAnalysisSortDirection>("desc");
  const [keywordUsageFilter, setKeywordUsageFilter] = useState<KeywordUsageFilter>("all");
  const [keywordTailFilter, setKeywordTailFilter] = useState<KeywordTailFilter>("all");
  const [keywordFavoritesOnly, setKeywordFavoritesOnly] = useState(false);
  const [keywordPickingIds, setKeywordPickingIds] = useState<Set<string>>(new Set());
  const [keywordFavoritingIds, setKeywordFavoritingIds] = useState<Set<string>>(new Set());
  const [keywordFeedbackIds, setKeywordFeedbackIds] = useState<Set<string>>(new Set());
  const keywordRejectTimers = useRef<Map<string, number>>(new Map());
  useEffect(() => () => {
    for (const timer of keywordRejectTimers.current.values()) window.clearTimeout(timer);
    keywordRejectTimers.current.clear();
  }, []);
  const [keywordLoading, setKeywordLoading] = useState(true);
  const [embroideryPage, setEmbroideryPage] = useState<SourcePlanPage>(EMPTY_SOURCE_PAGE);
  const [embroideryPageNumber, setEmbroideryPageNumber] = useState(1);
  const [embroideryPageSize, setEmbroideryPageSize] = useState(20);
  const [embroideryQuery, setEmbroideryQuery] = useState("");
  const [debouncedEmbroideryQuery, setDebouncedEmbroideryQuery] = useState("");
  const [embroideryLoading, setEmbroideryLoading] = useState(true);
  const [sourcePage, setSourcePage] = useState<SourcePlanPage>(EMPTY_SOURCE_PAGE);
  const [sourcePageNumber, setSourcePageNumber] = useState(1);
  const [sourcePageSize, setSourcePageSize] = useState(10);
  const [sourceQuery, setSourceQuery] = useState("");
  const [debouncedSourceQuery, setDebouncedSourceQuery] = useState("");
  const [stage3Query, setStage3Query] = useState("");
  const [debouncedStage3Query, setDebouncedStage3Query] = useState("");
  const [stage4Query, setStage4Query] = useState("");
  const [sourceSortBy, setSourceSortBy] = useState<SourcePlanSortBy>("source");
  const [sourceSortDirection, setSourceSortDirection] = useState<SourcePlanSortDirection>("asc");
  const [syncingSourcePlans, setSyncingSourcePlans] = useState(false);
  const [sourcePageLoading, setSourcePageLoading] = useState(true);
  const [reviewingReferenceIds, setReviewingReferenceIds] = useState<Set<string>>(new Set());
  const [sourcePlanMessage, setSourcePlanMessage] = useState("");
  const [stage2Jobs, setStage2Jobs] = useState<Stage2Job[]>([]);
  const [creatingStage2PlanIds, setCreatingStage2PlanIds] = useState<Set<string>>(new Set());
  const [cancellingStage2PlanIds, setCancellingStage2PlanIds] = useState<Set<string>>(new Set());
  const [stage2Message, setStage2Message] = useState("");
  const [stage3Groups, setStage3Groups] = useState<Stage3ReviewGroupList>(EMPTY_STAGE3_GROUPS);
  const [stage3Loading, setStage3Loading] = useState(true);
  const [stage3Analyzing, setStage3Analyzing] = useState(false);
  const [stage3Message, setStage3Message] = useState("");
  const [error, setError] = useState("");
  const [activeStage, setActiveStage] = useState<RrugcStageTab>("stage0");
  const [skillManagerOpen, setSkillManagerOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [skillCatalogRevision, setSkillCatalogRevision] = useState(0);
  const groupsStageActive = activeStage === "stage2" || activeStage === "stage3";
  const activeSourceQuery = activeStage === "stage3" ? debouncedStage3Query : debouncedSourceQuery;
  const visibleStage2SourcePlanIds = useMemo(
    () => Array.from(new Set(
      sourcePage.items.flatMap(plan => [
        plan.id,
        ...(plan.source_group_images || []).map(member => member.id),
      ]),
    )),
    [sourcePage.items],
  );
  const visibleStage2SourcePlanIdsKey = visibleStage2SourcePlanIds.join(",");

  const sourcePageCount = useMemo(
    () => Math.max(1, Math.ceil(sourcePage.total / Math.max(1, sourcePageSize))),
    [sourcePage.total, sourcePageSize],
  );

  async function refreshKeywordAnalysis(signal?: AbortSignal) {
    const result = await listKeywordAnalysis(
      {
        page: keywordPageNumber,
        pageSize: keywordPageSize,
        query: debouncedKeywordQuery,
        sortBy: keywordSortBy,
        sortDirection: keywordSortDirection,
        usage: keywordUsageFilter,
        tail: keywordTailFilter,
        favoritesOnly: keywordFavoritesOnly,
      },
      signal,
    );
    setKeywordPage(result);
  }

  async function changeKeywordPicked(keywordId: string, picked: boolean) {
    setKeywordPickingIds(current => new Set(current).add(keywordId));
    setError("");
    try {
      const updated = await setKeywordAnalysisPicked(keywordId, picked);
      setKeywordPage(current => ({
        ...current,
        items: current.items.map(item => item.id === updated.id ? updated : item),
      }));
      await refreshKeywordAnalysis();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to update keyword usage.");
    } finally {
      setKeywordPickingIds(current => {
        const next = new Set(current);
        next.delete(keywordId);
        return next;
      });
    }
  }

  async function changeKeywordFavorite(keywordId: string, favorite: boolean) {
    setKeywordFavoritingIds(current => new Set(current).add(keywordId));
    setError("");
    try {
      const updated = await setKeywordAnalysisFavorite(keywordId, favorite);
      setKeywordPage(current => ({
        ...current,
        items: current.items.map(item => item.id === updated.id ? updated : item),
      }));
      await refreshKeywordAnalysis();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to update keyword favorite.");
    } finally {
      setKeywordFavoritingIds(current => {
        const next = new Set(current);
        next.delete(keywordId);
        return next;
      });
    }
  }

  async function changeKeywordFeedback(keywordId: string, action: ScoutFeedbackAction, scope: ScoutFeedbackScope) {
    setKeywordFeedbackIds(current => new Set(current).add(keywordId));
    setError("");
    try {
      const updated = await setKeywordScoutFeedback(keywordId, action, scope);
      setKeywordPage(current => ({
        ...current,
        items: current.items.map(item => item.id === updated.id ? updated : item),
      }));
      const previousTimer = keywordRejectTimers.current.get(keywordId);
      if (previousTimer !== undefined) window.clearTimeout(previousTimer);
      keywordRejectTimers.current.delete(keywordId);
      if (action === "blocked") {
        // Match the server's 10-second grace period. This keeps the UI
        // responsive even when the 5-second Stage 0 poll is delayed.
        const timer = window.setTimeout(() => {
          keywordRejectTimers.current.delete(keywordId);
          setKeywordPage(current => ({
            ...current,
            items: current.items.filter(item => item.id !== keywordId),
            total: Math.max(0, current.total - (current.items.some(item => item.id === keywordId) ? 1 : 0)),
          }));
          // The existing Stage 0 polling will replenish the correct page
          // without a stale request from the click-time filter closure.
        }, 10_100);
        keywordRejectTimers.current.set(keywordId, timer);
      }
      await refreshKeywordAnalysis();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to update Scout feedback.");
    } finally {
      setKeywordFeedbackIds(current => {
        const next = new Set(current);
        next.delete(keywordId);
        return next;
      });
    }
  }

  function changeKeywordSort(next: KeywordAnalysisSortBy) {
    setKeywordLoading(true);
    setKeywordPageNumber(1);
    if (keywordSortBy === next) {
      setKeywordSortDirection(current => current === "asc" ? "desc" : "asc");
      return;
    }
    setKeywordSortBy(next);
    setKeywordSortDirection(
      next === "keyword" || next === "competition" ? "asc" : "desc",
    );
  }

  async function refreshEmbroideryDesigns(signal?: AbortSignal) {
    const result = await listSourcePlans(
      {
        page: embroideryPageNumber,
        pageSize: embroideryPageSize,
        query: debouncedEmbroideryQuery,
        sortBy: "source",
        sortDirection: "asc",
        sourcePrefix: "embroidery_",
      },
      signal,
    );
    const nextFingerprint = sourcePlanPageRenderFingerprint(result);
    setEmbroideryPage(current => (
      sourcePlanPageRenderFingerprint(current) === nextFingerprint
        ? current
        : result
    ));
  }

  async function refreshSourcePlans(signal?: AbortSignal) {
    const result = await listSourcePlans(
      {
        page: sourcePageNumber,
        pageSize: sourcePageSize,
        query: activeSourceQuery,
        sortBy: sourceSortBy,
        sortDirection: sourceSortDirection,
      },
      signal,
    );
    const nextFingerprint = sourcePlanPageRenderFingerprint(result);
    setSourcePage(current => (
      sourcePlanPageRenderFingerprint(current) === nextFingerprint
        ? current
        : result
    ));
  }

  async function refreshStage3Groups(signal?: AbortSignal) {
    const result = await listStage3ReviewGroups(signal);
    setStage3Groups(result);
  }

  async function analyzeStage3(folderId?: string) {
    setStage3Analyzing(true);
    setStage3Message("");
    setError("");
    try {
      const result = await analyzeStage3ReviewGroups(folderId);
      setStage3Message(
        result.queued > 0
          ? `Queued ${result.queued} of ${result.eligible} image${result.eligible === 1 ? "" : "s"} for UGC analysis.`
          : `All ${result.eligible} eligible image${result.eligible === 1 ? "" : "s"} are already queued or analyzed.`,
      );
      await refreshStage3Groups();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to queue Stage 4 analysis.");
    } finally {
      setStage3Analyzing(false);
    }
  }

  async function refreshStage2Jobs(signal?: AbortSignal) {
    if (visibleStage2SourcePlanIds.length === 0) {
      setStage2Jobs([]);
      return;
    }
    const result = await listStage2Jobs(visibleStage2SourcePlanIds, signal);
    const nextFingerprint = stage2JobsRenderFingerprint(result);
    setStage2Jobs(current => (
      stage2JobsRenderFingerprint(current) === nextFingerprint
        ? current
        : result
    ));
  }

  async function queueStage2Job(
    plan: SourcePlan,
    candidateIds: string[],
    skill: Stage2SkillSelection,
  ) {
    if (creatingStage2PlanIds.has(plan.id) || candidateIds.length === 0) return;
    setCreatingStage2PlanIds(current => new Set(current).add(plan.id));
    setStage2Message("");
    setError("");
    try {
      const batches = stage2ReferenceBatches(candidateIds);
      const results = await Promise.allSettled(
        batches.map(batch => createStage2Job(plan.id, batch, skill)),
      );
      const queued = results.filter(
        (result): result is PromiseFulfilledResult<Awaited<ReturnType<typeof createStage2Job>>> =>
          result.status === "fulfilled",
      );
      const failed = results.filter(
        (result): result is PromiseRejectedResult => result.status === "rejected",
      );
      if (queued.length === 0 && failed.length > 0) {
        throw failed[0].reason;
      }
      const createdCount = queued.filter(result => result.value.created).length;
      const existingCount = queued.length - createdCount;
      setStage2Message(
        createdCount + " generation batch"
        + (createdCount === 1 ? "" : "es")
        + " queued from " + candidateIds.length + " selected Pinterest ref"
        + (candidateIds.length === 1 ? "" : "s")
        + " · up to " + STAGE2_REFERENCES_PER_RUN + " refs + 1 random hat per run"
        + (existingCount ? "; " + existingCount + " already existed" : "")
        + ". You can cancel this batch for 10 seconds before generation starts.",
      );
      if (failed.length > 0) {
        setError(
          failed.length + " generation batch"
          + (failed.length === 1 ? "" : "es")
          + " could not be queued.",
        );
      }
      await Promise.all([
        refreshStage2Jobs(),
        refreshSourcePlans(),
      ]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to queue Stage 3 generation.");
    } finally {
      setCreatingStage2PlanIds(current => {
        const next = new Set(current);
        next.delete(plan.id);
        return next;
      });
    }
  }

  async function cancelStage2Batch(plan: SourcePlan) {
    if (cancellingStage2PlanIds.has(plan.id)) return;
    setCancellingStage2PlanIds(current => new Set(current).add(plan.id));
    setStage2Message("");
    setError("");
    try {
      const result = await cancelStage2Jobs(plan.id);
      setStage2Message(
        "Cancelled " + result.cancelled + " queued output generation"
        + (result.cancelled === 1 ? "" : "s")
        + " during the 10-second cancel window.",
      );
      await Promise.all([
        refreshStage2Jobs(),
        refreshSourcePlans(),
      ]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to cancel Stage 3 generation.");
      await refreshStage2Jobs().catch(() => undefined);
    } finally {
      setCancellingStage2PlanIds(current => {
        const next = new Set(current);
        next.delete(plan.id);
        return next;
      });
    }
  }

  async function syncDriveSourcePlans() {
    if (syncingSourcePlans) return;
    setSyncingSourcePlans(true);
    setSourcePlanMessage("");
    setError("");
    try {
      const result = await syncSourcePlans();
      setSourcePlanMessage(
        "Scanned " + result.folders_scanned + " folders / " + result.images_found + " images. "
        + result.jobs_queued + " source plans queued for AI context analysis"
        + (result.plans_missing ? "; " + result.plans_missing + " temporarily missing sources retained safely" : "")
        + "; current target " + result.target_count + " refs per source.",
      );
      await Promise.all([
        refreshSourcePlans(),
        refreshEmbroideryDesigns(),
      ]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to scan the embroidery source folder.");
    } finally {
      setSyncingSourcePlans(false);
    }
  }

  async function setSourceReferenceFeedback(
    plan: SourcePlan,
    reference: SourcePlanReferencePreview,
    requestedLabel: ReferenceManualLabel,
  ) {
    if (!plan.campaign_id || reviewingReferenceIds.has(reference.id)) return;
    const nextLabel = (
      (requestedLabel === "good" && reference.picked)
      || (requestedLabel === "bad" && reference.rejected)
    ) ? "clear" : requestedLabel;
    const nextPicked = nextLabel === "good";
    const nextRejected = nextLabel === "bad" || nextLabel === "ai";
    const removeFromPicker = nextLabel === "bad" || nextLabel === "ai";

    setReviewingReferenceIds(current => new Set(current).add(reference.id));
    setError("");
    setSourcePage(current => ({
      ...current,
      items: current.items.map(item => item.id !== plan.id ? item : {
        ...item,
        reference_previews: removeFromPicker
          ? item.reference_previews.filter(row => row.id !== reference.id)
          : item.reference_previews.map(row => (
            row.id === reference.id
              ? { ...row, picked: nextPicked, rejected: nextRejected }
              : row
          )),
      }),
    }));
    try {
      await markCandidateReferenceFeedback(
        plan.campaign_id,
        reference.id,
        nextLabel,
        nextLabel === "good"
          ? "Marked suitable as a preferred reference from source row " + plan.source_relative_path
          : nextLabel === "bad"
            ? "Marked unsuitable and unusable from source row " + plan.source_relative_path
            : "Cleared explicit reference feedback for source row " + plan.source_relative_path,
      );
      setSourcePlanMessage(
        nextLabel === "good"
          ? "Reference marked suitable. Future Scout runs will learn from this positive example."
          : nextLabel === "ai"
            ? "Reference marked AI-generated. It was removed from real refs and will train both AI detection and negative reference preference."
            : nextLabel === "bad"
              ? "Reference marked unsuitable. It was removed from the picker and will train negative preference."
              : "Reference feedback cleared. Unreviewed references remain usable by default without a strong training signal.",
      );
      await refreshSourcePlans();
    } catch (reason) {
      setSourcePage(current => ({
        ...current,
        items: current.items.map(item => item.id !== plan.id ? item : plan),
      }));
      setError(reason instanceof Error ? reason.message : "Unable to update reference feedback.");
    } finally {
      setReviewingReferenceIds(current => {
        const next = new Set(current);
        next.delete(reference.id);
        return next;
      });
    }
  }

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setKeywordPageNumber(1);
      setDebouncedKeywordQuery(keywordQuery.trim());
    }, 250);
    return () => window.clearTimeout(timer);
  }, [keywordQuery]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setSourcePageNumber(1);
      setDebouncedSourceQuery(sourceQuery.trim());
    }, 250);
    return () => window.clearTimeout(timer);
  }, [sourceQuery]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setSourcePageNumber(1);
      setDebouncedStage3Query(stage3Query.trim());
    }, 250);
    return () => window.clearTimeout(timer);
  }, [stage3Query]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setEmbroideryPageNumber(1);
      setDebouncedEmbroideryQuery(embroideryQuery.trim());
    }, 250);
    return () => window.clearTimeout(timer);
  }, [embroideryQuery]);

  useEffect(() => {
    if (sourcePageNumber > sourcePageCount) {
      setSourcePageNumber(sourcePageCount);
    }
  }, [sourcePageCount, sourcePageNumber]);

  useEffect(() => {
    if (activeStage !== "stage0") return;
    const controller = new AbortController();
    let refreshInFlight = true;
    setKeywordLoading(true);
    void refreshKeywordAnalysis(controller.signal)
      .catch(reason => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "Unable to load Stage 0 keyword analysis.");
        }
      })
      .finally(() => {
        refreshInFlight = false;
        if (!controller.signal.aborted) setKeywordLoading(false);
      });
    const timer = window.setInterval(() => {
      if (document.hidden || refreshInFlight) return;
      refreshInFlight = true;
      void refreshKeywordAnalysis()
        .catch(() => undefined)
        .finally(() => {
          refreshInFlight = false;
        });
    }, 5000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [activeStage, keywordPageNumber, keywordPageSize, debouncedKeywordQuery, keywordSortBy, keywordSortDirection, keywordUsageFilter, keywordTailFilter, keywordFavoritesOnly]);

  useEffect(() => {
    const pageCount = Math.max(1, Math.ceil(keywordPage.total / keywordPageSize));
    if (keywordPageNumber > pageCount && !keywordLoading) {
      setKeywordPageNumber(pageCount);
    }
  }, [keywordPage.total, keywordPageNumber, keywordPageSize, keywordLoading]);

  useEffect(() => {
    if (activeStage !== "stage1") return;
    const controller = new AbortController();
    let refreshInFlight = true;
    setEmbroideryLoading(true);
    void refreshEmbroideryDesigns(controller.signal)
      .catch(reason => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "Unable to load embroidery designs.");
        }
      })
      .finally(() => {
        refreshInFlight = false;
        if (!controller.signal.aborted) setEmbroideryLoading(false);
      });
    const timer = window.setInterval(() => {
      if (document.hidden || refreshInFlight) return;
      refreshInFlight = true;
      void refreshEmbroideryDesigns()
        .catch(() => undefined)
        .finally(() => {
          refreshInFlight = false;
        });
    }, 10000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [activeStage, embroideryPageNumber, embroideryPageSize, debouncedEmbroideryQuery]);

  useEffect(() => {
    if (!groupsStageActive) return;
    const controller = new AbortController();
    let refreshInFlight = true;
    setError("");
    if (sourcePage.items.length === 0 && sourcePage.total === 0) {
      setSourcePageLoading(true);
    }
    void refreshSourcePlans(controller.signal)
      .catch(reason => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "Unable to load source plans.");
        }
      })
      .finally(() => {
        refreshInFlight = false;
        if (!controller.signal.aborted) setSourcePageLoading(false);
      });
    const timer = window.setInterval(() => {
      if (document.hidden || refreshInFlight) return;
      refreshInFlight = true;
      void refreshSourcePlans()
        .catch(() => undefined)
        .finally(() => {
          refreshInFlight = false;
        });
    }, 5000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [groupsStageActive, activeStage, sourcePageNumber, sourcePageSize, activeSourceQuery, sourceSortBy, sourceSortDirection]);

  useEffect(() => {
    if (activeStage !== "stage3") return;
    const controller = new AbortController();
    let refreshInFlight = true;
    void refreshStage2Jobs(controller.signal)
      .catch(reason => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "Unable to load Stage 3 jobs.");
        }
      })
      .finally(() => {
        refreshInFlight = false;
      });
    const timer = window.setInterval(() => {
      if (document.hidden || refreshInFlight) return;
      refreshInFlight = true;
      void refreshStage2Jobs()
        .catch(() => undefined)
        .finally(() => {
          refreshInFlight = false;
        });
    }, 5000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [activeStage, visibleStage2SourcePlanIdsKey]);

  useEffect(() => {
    if (activeStage !== "stage4") return;
    const controller = new AbortController();
    let refreshInFlight = true;
    setStage3Loading(true);
    void refreshStage3Groups(controller.signal)
      .catch(reason => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "Unable to load Stage 4 review groups.");
        }
      })
      .finally(() => {
        refreshInFlight = false;
        if (!controller.signal.aborted) setStage3Loading(false);
      });
    const timer = window.setInterval(() => {
      if (document.hidden || refreshInFlight) return;
      refreshInFlight = true;
      void refreshStage3Groups()
        .catch(() => undefined)
        .finally(() => {
          refreshInFlight = false;
        });
    }, 5000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [activeStage]);

  function selectStage(nextStage: RrugcStageTab) {
    if (nextStage === "stage2" || nextStage === "stage3") {
      setSourcePageNumber(1);
      setSourcePageLoading(true);
    }
    setActiveStage(nextStage);
    window.requestAnimationFrame?.(() => {
      document.getElementById("rrugc-tab-" + nextStage)?.focus();
    });
  }

  function handleStageTabsKeyDown(event: KeyboardEvent<HTMLElement>) {
    const currentIndex = RRUGC_STAGE_TABS.findIndex(item => item.id === activeStage);
    let nextIndex = currentIndex;
    if (event.key === "ArrowRight") nextIndex = (currentIndex + 1) % RRUGC_STAGE_TABS.length;
    else if (event.key === "ArrowLeft") nextIndex = (currentIndex - 1 + RRUGC_STAGE_TABS.length) % RRUGC_STAGE_TABS.length;
    else if (event.key === "Home") nextIndex = 0;
    else if (event.key === "End") nextIndex = RRUGC_STAGE_TABS.length - 1;
    else return;
    event.preventDefault();
    selectStage(RRUGC_STAGE_TABS[nextIndex].id);
  }

  return <main className="rrugc-shell rrugc-source-first-shell">
    <aside className="ops-sidebar">
      <div className="brand"><b><BrandIcon /></b><span><strong>Creative assets</strong><small>UGC reference automation</small></span></div>
      <WorkspaceNavigation active="realistic-review-ugc" />
    </aside>
    <section className="rrugc-main">
      <WorkspacePageHeader
        className="rrugc-header"
        route="realistic-review-ugc"
        description="Analyze keyword demand, prepare embroidery designs across 13 hat colors, discover Pinterest references, generate UGC images, then build review-ready outputs."
        titleAddon={<span className="rrugc-page-live-pill"><i aria-hidden="true" />Dual scout pipeline</span>}
        actions={<div className="rrugc-global-management-actions">
          <button
            type="button"
            className="rrugc-global-management-button"
            onClick={() => setSkillManagerOpen(true)}
          >Manage skills</button>
          <button
            type="button"
            className="rrugc-global-management-button"
            onClick={() => setSettingsOpen(true)}
          >Settings</button>
          <WorkspaceBackToAssets />
        </div>}
      />
      <div className="rrugc-stage-tabs-shell">
        <nav
          className="ops-tabs rrugc-stage-tabs"
          aria-label="Realistic Review UGC sections"
          role="tablist"
          onKeyDown={handleStageTabsKeyDown}
        >
          {RRUGC_STAGE_TABS.map(item => (
            <button
              key={item.id}
              id={"rrugc-tab-" + item.id}
              type="button"
              role="tab"
              aria-selected={activeStage === item.id}
              aria-controls={"rrugc-panel-" + item.id}
              tabIndex={activeStage === item.id ? 0 : -1}
              className={activeStage === item.id ? "active" : ""}
              onClick={() => selectStage(item.id)}
            >
              <span className="rrugc-stage-tab-number" aria-hidden="true">{item.marker}</span>
              <span className="rrugc-stage-tab-copy"><strong>{item.label}</strong><small>{item.description}</small></span>
            </button>
          ))}
        </nav>
      </div>

      <div className="rrugc-page-body rrugc-source-first-body">
        {error && <div className="rrugc-error" role="alert">{error}</div>}

        <section
          id="rrugc-panel-stage0"
          className="rrugc-stage-panel"
          role="tabpanel"
          aria-labelledby="rrugc-tab-stage0"
          tabIndex={activeStage === "stage0" ? 0 : -1}
          hidden={activeStage !== "stage0"}
        >
          <KeywordAnalysisTable
            data={keywordPage}
            query={keywordQuery}
            loading={keywordLoading}
            sortBy={keywordSortBy}
            sortDirection={keywordSortDirection}
            usageFilter={keywordUsageFilter}
            tailFilter={keywordTailFilter}
            favoritesOnly={keywordFavoritesOnly}
            pickingIds={keywordPickingIds}
            favoritingIds={keywordFavoritingIds}
            feedbackUpdatingIds={keywordFeedbackIds}
            onFeedbackChange={changeKeywordFeedback}
            onSortChange={changeKeywordSort}
            onUsageFilterChange={value => {
              setKeywordLoading(true);
              setKeywordPageNumber(1);
              setKeywordUsageFilter(value);
            }}
            onPickChange={changeKeywordPicked}
            onFavoriteChange={changeKeywordFavorite}
            onTailFilterChange={value => {
              setKeywordLoading(true);
              setKeywordPageNumber(1);
              setKeywordTailFilter(value);
            }}
            onFavoritesOnlyChange={value => {
              setKeywordLoading(true);
              setKeywordPageNumber(1);
              setKeywordFavoritesOnly(value);
            }}
            onResetAll={() => {
              setKeywordLoading(true);
              setKeywordPageNumber(1);
              setKeywordQuery("");
              setDebouncedKeywordQuery("");
              setKeywordUsageFilter("all");
              setKeywordTailFilter("all");
              setKeywordFavoritesOnly(false);
            }}
            onPageChange={value => {
              setKeywordLoading(true);
              setKeywordPageNumber(value);
            }}
            onPageSizeChange={value => {
              setKeywordLoading(true);
              setKeywordPageNumber(1);
              setKeywordPageSize(value);
            }}
            onQueryChange={setKeywordQuery}
          />
        </section>

        <section
          id="rrugc-panel-stage1"
          className="rrugc-stage-panel"
          role="tabpanel"
          aria-labelledby="rrugc-tab-stage1"
          tabIndex={activeStage === "stage1" ? 0 : -1}
          hidden={activeStage !== "stage1"}
        >
          <EmbroideryColorwayStage
            active={activeStage === "stage1"}
            skillCatalogRevision={skillCatalogRevision}
            data={embroideryPage}
            loading={embroideryLoading}
            syncing={syncingSourcePlans}
            query={embroideryQuery}
            message={sourcePlanMessage}
            onSync={() => void syncDriveSourcePlans()}
            onQueryChange={setEmbroideryQuery}
            onPageChange={value => {
              setEmbroideryLoading(true);
              setEmbroideryPageNumber(value);
            }}
            onPageSizeChange={value => {
              setEmbroideryLoading(true);
              setEmbroideryPageNumber(1);
              setEmbroideryPageSize(value);
            }}
          />
        </section>

        <section
          id="rrugc-panel-stage2"
          className="rrugc-stage-panel"
          role="tabpanel"
          aria-labelledby="rrugc-tab-stage2"
          tabIndex={activeStage === "stage2" ? 0 : -1}
          hidden={activeStage !== "stage2"}
        >
          <SourcePlanTable
            plans={sourcePage.items}
            total={sourcePage.total}
            overview={sourcePage.overview}
            page={sourcePageNumber}
            pageSize={sourcePageSize}
            query={sourceQuery}
            sortBy={sourceSortBy}
            sortDirection={sourceSortDirection}
            syncing={syncingSourcePlans}
            loading={sourcePageLoading}
            reviewingReferenceIds={reviewingReferenceIds}
            message={sourcePlanMessage}
            onSync={() => void syncDriveSourcePlans()}
            onPageChange={value => {
              setSourcePageLoading(true);
              setSourcePageNumber(value);
            }}
            onPageSizeChange={value => {
              setSourcePageLoading(true);
              setSourcePageNumber(1);
              setSourcePageSize(value);
            }}
            onQueryChange={setSourceQuery}
            onSortByChange={value => {
              setSourcePageLoading(true);
              setSourcePageNumber(1);
              setSourceSortBy(value);
            }}
            onSortDirectionChange={value => {
              setSourcePageLoading(true);
              setSourcePageNumber(1);
              setSourceSortDirection(value);
            }}
            onSetReferenceFeedback={(plan, reference, label) => void setSourceReferenceFeedback(plan, reference, label)}
          />
        </section>

        <section
          id="rrugc-panel-stage3"
          className="rrugc-stage-panel"
          role="tabpanel"
          aria-labelledby="rrugc-tab-stage3"
          tabIndex={activeStage === "stage3" ? 0 : -1}
          hidden={activeStage !== "stage3"}
        >
          <Stage2JobTable
            plans={sourcePage.items}
            total={sourcePage.total}
            overview={sourcePage.overview}
            page={sourcePageNumber}
            pageSize={sourcePageSize}
            jobs={stage2Jobs}
            creatingPlanIds={creatingStage2PlanIds}
            cancellingPlanIds={cancellingStage2PlanIds}
            loading={sourcePageLoading}
            message={stage2Message}
            skillCatalogRevision={skillCatalogRevision}
            query={stage3Query}
            onQueryChange={setStage3Query}
            onManageSkills={() => setSkillManagerOpen(true)}
            onPageChange={value => {
              setSourcePageLoading(true);
              setSourcePageNumber(value);
            }}
            onPageSizeChange={value => {
              setSourcePageLoading(true);
              setSourcePageNumber(1);
              setSourcePageSize(value);
            }}
            onCreateJob={(plan, candidateIds, skill) => void queueStage2Job(plan, candidateIds, skill)}
            onCancelJobs={plan => void cancelStage2Batch(plan)}
          />
        </section>

        <section
          id="rrugc-panel-stage4"
          className="rrugc-stage-panel"
          role="tabpanel"
          aria-labelledby="rrugc-tab-stage4"
          tabIndex={activeStage === "stage4" ? 0 : -1}
          hidden={activeStage !== "stage4"}
        >
          <Stage3ReviewGroups
            data={stage3Groups}
            loading={stage3Loading}
            analyzing={stage3Analyzing}
            message={stage3Message}
            query={stage4Query}
            onQueryChange={setStage4Query}
            onAnalyze={folderId => void analyzeStage3(folderId)}
          />
        </section>

      </div>
      <SkillManagerModal
        open={skillManagerOpen}
        onClose={() => setSkillManagerOpen(false)}
        onChanged={() => setSkillCatalogRevision(current => current + 1)}
      />
      <RrugcWorkflowSettingsModal
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        onError={setError}
      />
    </section>
  </main>;
}
