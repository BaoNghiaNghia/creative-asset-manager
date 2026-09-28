import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { routeForPath } from "../AppRoute";
import { CampaignDeliveryPanel, CampaignExportPanel } from "./CampaignGenerationPanel";
import { DeliveryOperationsView } from "./DeliveryOperationsPanel";
import { scoutCommand } from "./RealisticReviewUgcPage";

describe("Realistic Review UGC route", () => {
  it("routes the dedicated top-level workspace", () => {
    expect(routeForPath("/realistic-review-ugc")).toBe("realistic-review-ugc");
    expect(routeForPath("/realistic-review-ugc/")).toBe("realistic-review-ugc");
  });

  it("builds a local scout command without changing the API host", () => {
    const command = scoutCommand("https://creative.example/", "campaign-1", "secret-token");
    expect(command).toContain("--base-url \"https://creative.example\"");
    expect(command).toContain("--campaign-id \"campaign-1\"");
    expect(command).toContain("--token \"secret-token\"");
    expect(command).toContain("--profile-dir");
  });

  it("renders Phase 9 export readiness and catalog counts", () => {
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
    expect(markup).toContain("PHASE 9 · EXPORT + CATALOG");
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


  it("renders Phase 10 delivery status and destination controls", () => {
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
    expect(markup).toContain("PHASE 10 · DELIVERY + LIFECYCLE");
    expect(markup).toContain("Deliver cataloged (4)");
    expect(markup).toContain("Paid Social Finals");
    expect(markup).toContain("retry needed");
    expect(markup).toContain("3/4 delivered");
  });



  it("renders Phase 11 delivery operations and internal events", () => {
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
    expect(markup).toContain("PHASE 11 · DELIVERY OPERATIONS");
    expect(markup).toContain("Automation on · every 5m");
    expect(markup).toContain("Run maintenance now");
    expect(markup).toContain("package partial failed");
    expect(markup).toContain("Retry exhausted");
  });

  it("disables Phase 11 manual maintenance when automation is off", () => {
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
