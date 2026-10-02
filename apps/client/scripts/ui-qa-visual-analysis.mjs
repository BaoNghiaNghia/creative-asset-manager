const INTERACTIVE_TAGS = new Set([
  "a",
  "button",
  "input",
  "select",
  "textarea",
  "summary",
  "video",
]);

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function round(value, digits = 3) {
  const scale = 10 ** digits;
  return Math.round(value * scale) / scale;
}

function rectArea(rect) {
  return Math.max(0, rect.width) * Math.max(0, rect.height);
}

function intersectionArea(a, b) {
  const left = Math.max(a.x, b.x);
  const top = Math.max(a.y, b.y);
  const right = Math.min(a.x + a.width, b.x + b.width);
  const bottom = Math.min(a.y + a.height, b.y + b.height);
  if (right <= left || bottom <= top) return 0;
  return (right - left) * (bottom - top);
}

function screenZone(rect, width, height) {
  const centerX = rect.x + rect.width / 2;
  const centerY = rect.y + rect.height / 2;
  if (centerY <= height * 0.18) return "top";
  if (centerY >= height * 0.86) return "bottom";
  if (centerX <= width * 0.2) return "left";
  if (centerX >= width * 0.82) return "right";
  return "main";
}

export function buildChangedMask(diffMaskPng) {
  const { width, height, data } = diffMaskPng;
  const mask = new Uint8Array(width * height);
  for (let index = 0; index < width * height; index += 1) {
    if (data[index * 4 + 3] > 0) {
      mask[index] = 1;
    }
  }
  return mask;
}

export function clusterChangedRegions(
  mask,
  width,
  height,
  {
    tileSize = 12,
    joinRadiusTiles = 2,
    minPixels = 16,
    maxRegions = 8,
    padding = 4,
  } = {},
) {
  if (!(mask instanceof Uint8Array) || mask.length !== width * height) {
    throw new Error("Visual diff mask dimensions do not match the screenshot.");
  }

  const columns = Math.ceil(width / tileSize);
  const activeTiles = new Map();

  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      if (mask[y * width + x] === 0) continue;
      const tileX = Math.floor(x / tileSize);
      const tileY = Math.floor(y / tileSize);
      const key = tileY * columns + tileX;
      let tile = activeTiles.get(key);
      if (!tile) {
        tile = {
          tileX,
          tileY,
          changedPixels: 0,
          minX: x,
          minY: y,
          maxX: x,
          maxY: y,
        };
        activeTiles.set(key, tile);
      }
      tile.changedPixels += 1;
      tile.minX = Math.min(tile.minX, x);
      tile.minY = Math.min(tile.minY, y);
      tile.maxX = Math.max(tile.maxX, x);
      tile.maxY = Math.max(tile.maxY, y);
    }
  }

  const visited = new Set();
  const regions = [];

  for (const [startKey, startTile] of activeTiles) {
    if (visited.has(startKey)) continue;
    visited.add(startKey);
    const queue = [startTile];
    let changedPixels = 0;
    let minX = width;
    let minY = height;
    let maxX = 0;
    let maxY = 0;
    let tileCount = 0;

    while (queue.length > 0) {
      const tile = queue.pop();
      changedPixels += tile.changedPixels;
      tileCount += 1;
      minX = Math.min(minX, tile.minX);
      minY = Math.min(minY, tile.minY);
      maxX = Math.max(maxX, tile.maxX);
      maxY = Math.max(maxY, tile.maxY);

      for (let dy = -joinRadiusTiles; dy <= joinRadiusTiles; dy += 1) {
        for (let dx = -joinRadiusTiles; dx <= joinRadiusTiles; dx += 1) {
          if (dx === 0 && dy === 0) continue;
          const nextX = tile.tileX + dx;
          const nextY = tile.tileY + dy;
          if (nextX < 0 || nextY < 0) continue;
          const nextKey = nextY * columns + nextX;
          if (visited.has(nextKey)) continue;
          const nextTile = activeTiles.get(nextKey);
          if (!nextTile) continue;
          visited.add(nextKey);
          queue.push(nextTile);
        }
      }
    }

    if (changedPixels < minPixels) continue;
    const x = clamp(minX - padding, 0, Math.max(0, width - 1));
    const y = clamp(minY - padding, 0, Math.max(0, height - 1));
    const right = clamp(maxX + 1 + padding, 1, width);
    const bottom = clamp(maxY + 1 + padding, 1, height);
    const region = {
      x,
      y,
      width: Math.max(1, right - x),
      height: Math.max(1, bottom - y),
      changedPixels,
      tileCount,
    };
    region.density = round(changedPixels / rectArea(region), 4);
    region.screenRatio = round(changedPixels / (width * height), 6);
    region.zone = screenZone(region, width, height);
    regions.push(region);
  }

  return regions
    .sort((a, b) => b.changedPixels - a.changedPixels)
    .slice(0, maxRegions)
    .map((region, index) => ({ id: index + 1, ...region }));
}

function rankElementCandidates(region, elements, screenshotWidth, screenshotHeight) {
  const regionArea = Math.max(1, rectArea(region));
  const screenArea = Math.max(1, screenshotWidth * screenshotHeight);
  const candidates = [];

  for (const element of elements || []) {
    const elementArea = rectArea(element.rect || {});
    if (elementArea <= 0) continue;
    const overlap = intersectionArea(region, element.rect);
    if (overlap <= 0) continue;

    const regionCoverage = overlap / regionArea;
    const elementCoverage = overlap / elementArea;
    const specificity = 1 - Math.min(1, elementArea / screenArea);
    const semanticBoost =
      element.id ||
      element.testId ||
      element.ariaLabel ||
      element.role ||
      INTERACTIVE_TAGS.has(element.tag)
        ? 0.12
        : 0;
    const score =
      regionCoverage * 0.55 +
      elementCoverage * 0.25 +
      specificity * 0.08 +
      semanticBoost;

    candidates.push({
      selector: element.selector,
      tag: element.tag,
      id: element.id || null,
      testId: element.testId || null,
      ariaLabel: element.ariaLabel || null,
      role: element.role || null,
      classes: element.classes || [],
      score: round(score),
      regionCoverage: round(regionCoverage),
      elementCoverage: round(elementCoverage),
    });
  }

  const seen = new Set();
  return candidates
    .sort((a, b) => b.score - a.score)
    .filter((candidate) => {
      if (!candidate.selector || seen.has(candidate.selector)) return false;
      seen.add(candidate.selector);
      return true;
    })
    .slice(0, 3);
}

function overlappingActions(region, actionTargets) {
  const regionArea = Math.max(1, rectArea(region));
  return (actionTargets || [])
    .map((target) => {
      const overlap = intersectionArea(region, target.rect || {});
      return {
        action: target.action,
        selector: target.selector,
        overlapRatio: round(overlap / regionArea),
      };
    })
    .filter((target) => target.overlapRatio > 0)
    .sort((a, b) => b.overlapRatio - a.overlapRatio);
}

export function enrichRegions(
  regions,
  elements,
  actionTargets,
  screenshotWidth,
  screenshotHeight,
) {
  return (regions || []).map((region) => ({
    ...region,
    zone: region.zone || screenZone(region, screenshotWidth, screenshotHeight),
    actionTargets: overlappingActions(region, actionTargets),
    likelyElements: rankElementCandidates(
      region,
      elements,
      screenshotWidth,
      screenshotHeight,
    ),
  }));
}

export function paintRegionBoxes(
  rgba,
  width,
  height,
  regions,
  { thickness = 3, color = [255, 54, 54, 255] } = {},
) {
  if (!rgba || rgba.length !== width * height * 4) {
    throw new Error("RGBA buffer dimensions do not match the screenshot.");
  }

  const setPixel = (x, y) => {
    if (x < 0 || y < 0 || x >= width || y >= height) return;
    const offset = (y * width + x) * 4;
    rgba[offset] = color[0];
    rgba[offset + 1] = color[1];
    rgba[offset + 2] = color[2];
    rgba[offset + 3] = color[3];
  };

  for (const region of regions || []) {
    const left = clamp(Math.floor(region.x), 0, width - 1);
    const top = clamp(Math.floor(region.y), 0, height - 1);
    const right = clamp(Math.ceil(region.x + region.width - 1), 0, width - 1);
    const bottom = clamp(Math.ceil(region.y + region.height - 1), 0, height - 1);
    for (let offset = 0; offset < thickness; offset += 1) {
      for (let x = left; x <= right; x += 1) {
        setPixel(x, top + offset);
        setPixel(x, bottom - offset);
      }
      for (let y = top; y <= bottom; y += 1) {
        setPixel(left + offset, y);
        setPixel(right - offset, y);
      }
    }
  }
}

function unique(values) {
  return [...new Set(values.filter(Boolean))];
}

function tokensFromSelector(selector) {
  if (!selector) return [];
  const tokens = [];
  for (const match of selector.matchAll(/[.#]([a-zA-Z_][\w-]{2,})/g)) {
    tokens.push(match[1]);
  }
  for (const match of selector.matchAll(
    /\[(?:aria-label|data-testid|id)=["']([^"']{3,})["']\]/g,
  )) {
    tokens.push(match[1]);
  }
  return tokens;
}

export function issueSearchTokens(issue) {
  const tokens = [];
  for (const action of issue.actions || []) {
    tokens.push(...tokensFromSelector(action.selector));
  }
  for (const region of issue.regions || []) {
    for (const element of region.likelyElements || []) {
      tokens.push(...(element.classes || []));
      tokens.push(element.id, element.testId, element.ariaLabel);
      tokens.push(...tokensFromSelector(element.selector));
    }
  }
  return unique(
    tokens
      .map((token) => String(token || "").trim())
      .filter((token) => token.length >= 3 && token.length <= 120),
  ).slice(0, 20);
}

export function createVisualAnalysis(report) {
  const issues = [];
  for (const result of report.results || []) {
    for (const comparison of result.visualComparisons || []) {
      if (
        !["missing-baseline", "dimension-mismatch", "mismatch"].includes(
          comparison.status,
        )
      ) {
        continue;
      }
      const likelySelectors = unique(
        (comparison.regions || []).flatMap((region) =>
          (region.likelyElements || []).map((element) => element.selector),
        ),
      ).slice(0, 8);
      issues.push({
        viewport: result.viewport,
        state: comparison.state || comparison.screenshot,
        screenshot: comparison.screenshot,
        status: comparison.status,
        mismatchedPixels: comparison.mismatchedPixels,
        diffRatio: comparison.diffRatio,
        actualSize: comparison.actualSize || result.size,
        baselineSize: comparison.baselineSize || result.size,
        baseline: comparison.baseline,
        diff: comparison.diff,
        diagnostic: comparison.diagnostic || null,
        actions: comparison.actions || [],
        regions: comparison.regions || [],
        likelySelectors,
        sourceHints: comparison.sourceHints || [],
      });
    }
  }

  return {
    schemaVersion: 1,
    runId: report.runId,
    status: issues.length > 0 ? "failed" : "passed",
    issueCount: issues.length,
    issues,
  };
}

function percent(value) {
  if (value === null || value === undefined || !Number.isFinite(value)) return "n/a";
  return `${(value * 100).toFixed(3)}%`;
}

function markdownCode(value) {
  return `\`${String(value).replaceAll("\`", "\\`")}\``;
}

export function renderVisualAnalysisMarkdown(analysis) {
  const lines = [
    "# Visual regression analysis",
    "",
    `Status: **${analysis.status.toUpperCase()}**`,
    `Issues: **${analysis.issueCount}**`,
    "",
  ];

  if (analysis.issueCount === 0) {
    lines.push("No visual regression issues were detected.", "");
    return lines.join("\n");
  }

  lines.push(
    "Use these diagnostics to repair the implementation first. Do not refresh baselines merely to make the gate pass.",
    "",
  );

  analysis.issues.forEach((issue, issueIndex) => {
    lines.push(
      `## ${issueIndex + 1}. ${issue.viewport} / ${issue.state}`,
      "",
      `- Status: **${issue.status}**`,
      `- Changed pixels: ${issue.mismatchedPixels ?? "n/a"} (${percent(issue.diffRatio)})`,
    );
    if (issue.diff) lines.push(`- Diff image: ${markdownCode(issue.diff)}`);
    if (issue.diagnostic) {
      lines.push(`- Annotated screenshot: ${markdownCode(issue.diagnostic)}`);
    }

    if ((issue.actions || []).length > 0) {
      lines.push("- State actions:");
      for (const action of issue.actions) {
        lines.push(
          `  - ${action.action}: ${markdownCode(action.selector)}`,
        );
      }
    }

    if ((issue.sourceHints || []).length > 0) {
      lines.push("- Likely source files:");
      for (const hint of issue.sourceHints) {
        lines.push(
          `  - ${markdownCode(hint.path)} (score ${hint.score}; matches: ${hint.matches.map(markdownCode).join(", ")})`,
        );
      }
    }

    if ((issue.regions || []).length > 0) {
      lines.push("- Affected regions:");
      for (const region of issue.regions) {
        lines.push(
          `  - Region #${region.id}: x=${region.x}, y=${region.y}, w=${region.width}, h=${region.height}; zone=${region.zone}; changed=${region.changedPixels}`,
        );
        for (const target of region.actionTargets || []) {
          lines.push(
            `    - action target: ${target.action} ${markdownCode(target.selector)} (overlap ${percent(target.overlapRatio)})`,
          );
        }
        for (const element of region.likelyElements || []) {
          lines.push(
            `    - likely element: ${markdownCode(element.selector)} (score ${element.score})`,
          );
        }
      }
    } else if (issue.status === "dimension-mismatch") {
      lines.push(
        `- Screenshot dimensions changed from ${issue.baselineSize.width}x${issue.baselineSize.height} to ${issue.actualSize.width}x${issue.actualSize.height}.`,
      );
    }

    lines.push("");
  });

  return lines.join("\n");
}
