import { describe, expect, it } from "vitest";
import { isKeywordArtworkSkill } from "./KeywordImageStage";
import type { Stage2Skill } from "./types";

const keyword = {
  source: "local", skill_id: null, skill_name: "gatorhats-keyword-embroidery",
  display_name: "GatorHats Keyword Embroidery", description: "",
  default_version: null, latest_version: null, local_version: null,
  synced_version: null, ready: true, keyword_artwork_ready: true,
  sync_state: "ready", version_options: [],
} satisfies Stage2Skill;

describe("Stage 1 keyword skill selection", () => {
  it("admits only installed keyword-artwork skills with no required reference images", () => {
    expect(isKeywordArtworkSkill(keyword)).toBe(true);
    expect(isKeywordArtworkSkill({ ...keyword, skill_name: "redesign-8869-v3", keyword_artwork_ready: false })).toBe(false);
    expect(isKeywordArtworkSkill({ ...keyword, ready: false })).toBe(false);
  });
});
