import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { StageSortControls } from "./StageSortControls";
import { sortStage5LoadedGroups } from "./Stage3ReviewGroups";
import type { Stage3ReviewGroup, Stage3ReviewImage } from "./types";

function image(id: string, date: string, score: number | null, status = "ready"): Stage3ReviewImage {
  return { stage2_job_id: id, completed_at: date, final_score: score,
    analysis_status: status } as Stage3ReviewImage;
}
function group(id: string, date: string, scores: (number | null)[], status = "ready"): Stage3ReviewGroup {
  return { folder_id: id, folder_name: id, folder_path: id,
    image_count: scores.length, status, latest_completed_at: date,
    images: scores.map((score, i) => image(id + i, date, score, status)),
  } as Stage3ReviewGroup;
}

describe("Stage 2–5 sorting", () => {
  it("offers keyboard accessible server-sort controls for Stage 2 and Stage 4", () => {
    const html = renderToStaticMarkup(<StageSortControls stage="Stage 4 groups"
      sortBy="active_jobs" direction="desc" options={["source", "active_jobs", "outputs"]}
      onChange={() => undefined} />);
    expect(html).toContain('aria-label="Sort Stage 4 groups by"');
    expect(html).toContain('value="active_jobs"');
    expect(html).toContain("Completed outputs");
    expect(html).toContain("Reverse sort order for Stage 4 groups");
  });
  it("sorts loaded Stage 5 folders and scores without mutating API data", () => {
    const originals = [
      group("Alpha", "2026-10-09T11:00:00Z", [0.6], "error"),
      group("Beta", "2026-10-13T11:00:00Z", [null, 0.96], "ready"),
      group("Gamma", "2026-10-12T11:00:00Z", [0.84, 0.8], "rejected"),
    ];
    const before = JSON.stringify(originals);
    expect(sortStage5LoadedGroups(originals, "newest").map(g => g.folder_id))
      .toEqual(["Beta", "Gamma", "Alpha"]);
    expect(sortStage5LoadedGroups(originals, "oldest").map(g => g.folder_id))
      .toEqual(["Alpha", "Gamma", "Beta"]);
    expect(sortStage5LoadedGroups(originals, "score").map(g => g.folder_id))
      .toEqual(["Beta", "Gamma", "Alpha"]);
    expect(sortStage5LoadedGroups(originals, "image_count").map(g => g.folder_id)[0])
      .toBe("Beta");
    expect(sortStage5LoadedGroups(originals, "status").map(g => g.folder_id))
      .toEqual(["Alpha", "Gamma", "Beta"]);
    expect(JSON.stringify(originals)).toBe(before);
  });
  it("keeps images without scores after scored ones", () => {
    const original = group("Mixed", "2026-10-13T10:00:00Z", [null, 0.9, 0.4]);
    const sorted = sortStage5LoadedGroups([original], "score");
    expect(sorted[0].images.map(i => i.final_score)).toEqual([0.9, 0.4, null]);
  });
});
