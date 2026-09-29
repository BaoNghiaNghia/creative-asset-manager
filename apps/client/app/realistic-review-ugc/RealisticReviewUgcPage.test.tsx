import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { routeForPath } from "../AppRoute";
import { CampaignDeliveryPanel, CampaignExportPanel } from "./CampaignGenerationPanel";
import { DeliveryOperationsView } from "./DeliveryOperationsPanel";
import { autoScoutBootstrapCommand, autoScoutCommand, scoutClientIsCurrent } from "./PinterestAutoScoutPanel";
import { candidateGalleryTab, candidateMatchesGalleryTab, candidatePhonePriority, RealisticReviewUgcPage, scoutCommand } from "./RealisticReviewUgcPage";
import { referenceLifestyleSearchQueries } from "./searchPresets";

describe("Realistic Review UGC route", () => {
  it("routes the dedicated top-level workspace", () => {
    expect(routeForPath("/realistic-review-ugc")).toBe("realistic-review-ugc");
    expect(routeForPath("/realistic-review-ugc/")).toBe("realistic-review-ugc");
  });

  it("renders one campaign management card with an add action", () => {
    const markup = renderToStaticMarkup(<RealisticReviewUgcPage />);
    expect(markup).toContain("Campaigns");
    expect(markup).toContain("＋ Add campaign");
    expect(markup).toContain("Add, edit, delete, and select campaigns from one place.");
    expect(markup).not.toContain("Define what Auto Scout should find");
  });

  it("detects outdated local Scout clients without blocking future versions", () => {
    expect(scoutClientIsCurrent("rrugc-scout-v7")).toBe(false);
    expect(scoutClientIsCurrent("rrugc-scout-v8")).toBe(true);
    expect(scoutClientIsCurrent("rrugc-scout-v9")).toBe(true);
    expect(scoutClientIsCurrent(null)).toBe(false);
  });

  it("keeps qualification status independent from Drive lifecycle", () => {
    expect(candidateGalleryTab("approved")).toBe("approved");
    expect(candidateGalleryTab("rejected_quality")).toBe("rejected");
    expect(candidateGalleryTab("rejected_duplicate")).toBe("rejected");
    expect(candidateGalleryTab("analysis_failed")).toBe("processing");
    expect(candidateGalleryTab("import_queued")).toBe("drive");
    expect(candidateGalleryTab("importing")).toBe("drive");
    expect(candidateGalleryTab("drive_ready")).toBe("drive");
    expect(candidateGalleryTab("import_failed")).toBe("drive");
    expect(candidateGalleryTab("analysis_queued")).toBe("processing");
    expect(candidateGalleryTab("analyzing")).toBe("processing");
    expect(candidateGalleryTab("needs_review")).toBe("processing");

    expect(candidateMatchesGalleryTab("import_queued", "approved")).toBe(true);
    expect(candidateMatchesGalleryTab("importing", "approved")).toBe(true);
    expect(candidateMatchesGalleryTab("drive_ready", "approved")).toBe(true);
    expect(candidateMatchesGalleryTab("import_failed", "approved")).toBe(true);
    expect(candidateMatchesGalleryTab("drive_ready", "drive")).toBe(true);
    expect(candidateMatchesGalleryTab("analysis_failed", "rejected")).toBe(false);
    expect(candidateMatchesGalleryTab("analysis_failed", "processing")).toBe(true);
    expect(candidateMatchesGalleryTab("needs_review", "processing")).toBe(true);
  });

  it("uses a balanced hat-person lifestyle Pinterest preset", () => {
    const queries = referenceLifestyleSearchQueries();
    expect(queries).toHaveLength(10);
    expect(queries).toContain("man wearing baseball cap candid phone photo");
    expect(queries).toContain("woman wearing bucket hat candid phone photo");
    expect(queries).toContain("couple wearing hats candid outdoor phone photo");
    expect(queries).toContain("family wearing hats candid outdoor natural light");
    expect(queries).toContain("friends wearing hats candid smartphone photo");
  });

  it("ranks smartphone-like references above artistic ones", () => {
    const base = {
      id: "candidate",
      campaign_id: "campaign",
      pin_url: "https://www.pinterest.com/pin/1/",
      image_url: "https://i.pinimg.com/736x/1.jpg",
      alt_text: null,
      status: "approved" as const,
      analysis_revision: 1,
      import_revision: 0,
      people_count: 1,
      primary_head_ratio: 0.35,
      smile_score: 0.2,
      head_visible: true,
      existing_headwear: true,
      head_occlusion: 0.1,
      mobile_ugc_score: 0.8,
      quality_score: 0.8,
      ai_risk_score: 0.05,
      product_fit_score: 0.8,
      final_score: 0.8,
      reject_reason: null,
      analyzer_provider: null,
      analyzer_model: null,
      analyzer_version: null,
      analysis_summary: null,
      analyzed_at: null,
      content_hash: null,
      width: null,
      height: null,
      size_bytes: null,
      remote_file_id: null,
      remote_folder_id: null,
      web_url: null,
      last_error_code: null,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    };
    expect(candidatePhonePriority({
      ...base,
      phone_authenticity_score: 0.9,
      artistic_editorial_risk: 0.1,
    })).toBeGreaterThan(candidatePhonePriority({
      ...base,
      phone_authenticity_score: 0.35,
      artistic_editorial_risk: 0.8,
    }));
    expect(candidatePhonePriority({
      ...base,
      phone_authenticity_score: 0.8,
      artistic_editorial_risk: 0.1,
      ai_manual_label: "real",
    })).toBeGreaterThan(candidatePhonePriority({
      ...base,
      phone_authenticity_score: 0.95,
      artistic_editorial_risk: 0.05,
      ai_manual_label: "ai",
    }));
    expect(candidatePhonePriority({
      ...base,
      phone_authenticity_score: 0.65,
      reference_manual_label: "good",
    })).toBeGreaterThan(candidatePhonePriority({
      ...base,
      phone_authenticity_score: 0.95,
      reference_manual_label: "bad",
    }));
  });

  it("builds a local scout command without changing the API host", () => {
    const command = scoutCommand("https://creative.example/", "campaign-1", "secret-token");
    expect(command).toContain("--base-url \"https://creative.example\"");
    expect(command).toContain("--campaign-id \"campaign-1\"");
    expect(command).toContain("--token \"secret-token\"");
    expect(command).toContain("--profile-dir");
  });

  it("builds bootstrap and Agent commands with the same persistent profile", () => {
    const profileDir = "D:\\Bot_Tool_Auto_Game\\scan_pinterest\\pinterest-profile";
    const bootstrap = autoScoutBootstrapCommand(profileDir);
    const command = autoScoutCommand(
      "https://creative.example/",
      "agent-1",
      "agent-secret",
      profileDir,
    );
    expect(bootstrap).toContain("--bootstrap-login");
    expect(bootstrap).toContain("--profile-dir \"" + profileDir + "\"");
    expect(command).toContain("--base-url \"https://creative.example\"");
    expect(command).toContain("--agent-id \"agent-1\"");
    expect(command).not.toContain("--campaign-id");
    expect(command).toContain("--token \"agent-secret\"");
    expect(command).toContain("--profile-dir \"" + profileDir + "\"");
  });

  it("renders export readiness and catalog counts", () => {
    const markup = renderToStaticMarkup(
      <CampaignExportPanel
        summary={{
          generated: 8,
          review_pending: 2,
          approved: 4,
          rejected: 2,
          export_ready: 3,
          exported: 1,
        }}
        busy={false}
        onExport={() => undefined}
      />,
    );
    expect(markup).toContain("CATALOG");
    expect(markup).toContain("Export ready (3)");
    expect(markup).toContain("Cataloged");
    expect(markup).toContain(">1<");
  });

  it("disables batch export when nothing is export ready", () => {
    const markup = renderToStaticMarkup(
      <CampaignExportPanel summary={null} busy={false} onExport={() => undefined} />,
    );
    expect(markup).toContain("disabled");
    expect(markup).toContain("Export ready (0)");
  });


  it("renders delivery status and destination controls", () => {
    const markup = renderToStaticMarkup(
      <CampaignDeliveryPanel
        summary={{
          campaign_status: "running",
          auto_complete_on_delivery: true,
          completion_destination_id: "destination-1",
          cataloged: 4,
          packages_total: 2,
          packages_delivered: 1,
          packages_partial_failed: 1,
          packages_expired: 0,
          latest_delivered_count: 3,
          latest_export_count: 4,
          auto_complete_eligible: false,
          completed_at: null,
        }}
        destinations={[{
          id: "destination-1",
          name: "Paid Social Finals",
          kind: "google_drive_folder",
          target_ref: "drive-folder-1",
          retention_days: 30,
          active: true,
          created_by_user_id: "user-a",
          created_at: "2026-09-28T00:00:00Z",
          updated_at: "2026-09-28T00:00:00Z",
          archived_at: null,
        }]}
        packages={[{
          id: "package-1",
          campaign_id: "campaign-1",
          destination_id: "destination-1",
          status: "partial_failed",
          export_count: 4,
          delivered_count: 3,
          failed_count: 1,
          auto_retry_count: 1,
          items: [],
          started_at: "2026-09-28T00:00:00Z",
          last_retry_at: "2026-09-28T00:04:00Z",
          next_retry_at: "2026-09-28T00:10:00Z",
          delivered_at: null,
          expires_at: null,
          expired_at: null,
          created_at: "2026-09-28T00:00:00Z",
          updated_at: "2026-09-28T00:00:00Z",
        }]}
        selectedDestinationId="destination-1"
        autoComplete={true}
        destinationName=""
        destinationFolderId=""
        retentionDays={90}
        busy=""
        onDestinationChange={() => undefined}
        onAutoCompleteChange={() => undefined}
        onDestinationNameChange={() => undefined}
        onDestinationFolderIdChange={() => undefined}
        onRetentionDaysChange={() => undefined}
        onCreateDestination={() => undefined}
        onDeliver={() => undefined}
        onSavePolicy={() => undefined}
        onReconcile={() => undefined}
      />,
    );
    expect(markup).toContain("Campaign delivery");
    expect(markup).toContain("Deliver cataloged (4)");
    expect(markup).toContain("Paid Social Finals");
    expect(markup).toContain("retry needed");
    expect(markup).toContain("3/4 delivered");
  });



  it("renders delivery operations and internal events", () => {
    const markup = renderToStaticMarkup(
      <DeliveryOperationsView
        summary={{
          automation_enabled: true,
          campaigns_total: 6,
          campaigns_completed: 4,
          destinations_active: 2,
          packages_total: 9,
          packages_delivered: 7,
          packages_partial_failed: 2,
          packages_expired: 1,
          retry_due: 1,
          retry_exhausted: 1,
          items_delivered: 24,
          items_failed: 2,
          latest_delivery_at: "2026-09-28T00:20:00Z",
          maintenance_interval_seconds: 300,
          auto_retry_max_attempts: 5,
          recent_events: [{
            id: "event-1",
            campaign_id: "campaign-1",
            package_id: "package-1",
            event_type: "package_partial_failed",
            severity: "warning",
            message: "Delivery has 1 failed item; automatic retry is scheduled.",
            payload: null,
            created_at: "2026-09-28T00:15:00Z",
          }],
        }}
        busy={false}
        onRun={() => undefined}
      />,
    );
    expect(markup).toContain("DELIVERY OPERATIONS");
    expect(markup).toContain("Automation on · every 5m");
    expect(markup).toContain("Run maintenance now");
    expect(markup).toContain("package partial failed");
    expect(markup).toContain("Retry exhausted");
  });

  it("disables manual maintenance when automation is off", () => {
    const markup = renderToStaticMarkup(
      <DeliveryOperationsView summary={null} busy={false} onRun={() => undefined} />,
    );
    expect(markup).toContain("Automation off");
    expect(markup).toContain("disabled");
  });


  it("disables delivery when no destination is selected", () => {
    const markup = renderToStaticMarkup(
      <CampaignDeliveryPanel
        summary={null}
        destinations={[]}
        packages={[]}
        selectedDestinationId=""
        autoComplete={false}
        destinationName=""
        destinationFolderId=""
        retentionDays={90}
        busy=""
        onDestinationChange={() => undefined}
        onAutoCompleteChange={() => undefined}
        onDestinationNameChange={() => undefined}
        onDestinationFolderIdChange={() => undefined}
        onRetentionDaysChange={() => undefined}
        onCreateDestination={() => undefined}
        onDeliver={() => undefined}
        onSavePolicy={() => undefined}
        onReconcile={() => undefined}
      />,
    );
    expect(markup).toContain("Deliver cataloged (0)");
    expect(markup).toContain("disabled");
  });
});
