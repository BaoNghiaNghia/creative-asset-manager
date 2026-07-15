import type { Asset } from "../types";

function normalizeText(value: string) {
  return value
    .normalize("NFD")
    .replace(/\p{Diacritic}/gu, "")
    .replace(/đ/g, "d")
    .toLowerCase()
    .trim();
}

function editDistance(left: string, right: string) {
  const previous = Array.from({ length: right.length + 1 }, (_, index) => index);

  for (let leftIndex = 1; leftIndex <= left.length; leftIndex += 1) {
    let diagonal = previous[0];
    previous[0] = leftIndex;

    for (let rightIndex = 1; rightIndex <= right.length; rightIndex += 1) {
      const above = previous[rightIndex];
      previous[rightIndex] = Math.min(
        previous[rightIndex] + 1,
        previous[rightIndex - 1] + 1,
        diagonal + (left[leftIndex - 1] === right[rightIndex - 1] ? 0 : 1),
      );
      diagonal = above;
    }
  }

  return previous[right.length];
}

function subsequenceScore(text: string, token: string) {
  if (token.length < 3) return null;

  let tokenIndex = 0;
  let start = -1;
  let end = -1;

  for (let index = 0; index < text.length && tokenIndex < token.length; index += 1) {
    if (text[index] !== token[tokenIndex]) continue;
    if (start < 0) start = index;
    end = index;
    tokenIndex += 1;
  }

  if (tokenIndex !== token.length) return null;
  const span = end - start + 1;
  if (span > token.length * 2.5) return null;
  return 150 - (span - token.length) * 8 - start;
}

function tokenScore(name: string, words: string[], token: string) {
  if (name === token) return 600;

  const index = name.indexOf(token);
  if (index >= 0) {
    if (index === 0) return 500;
    if (words.some(word => word.startsWith(token))) return 440;
    return 380 - Math.min(index, 80);
  }

  const subsequence = subsequenceScore(name, token);
  if (subsequence !== null) return subsequence;

  if (token.length < 4) return null;
  const allowedDistance = token.length >= 8 ? 2 : 1;
  const closestDistance = words.reduce(
    (closest, word) => Math.min(closest, editDistance(word, token)),
    Number.POSITIVE_INFINITY,
  );
  return closestDistance <= allowedDistance ? 110 - closestDistance * 20 : null;
}

export function searchAssets(items: Asset[], query: string) {
  const normalizedQuery = normalizeText(query);
  if (!normalizedQuery) return items;

  const tokens = normalizedQuery.split(/\s+/).filter(Boolean);
  const ranked = items.flatMap(item => {
    const normalizedName = normalizeText(item.name);
    const words = normalizedName.split(/[\s._\-()[\]{}]+/).filter(Boolean);
    let score = item.kind === "folder" ? 1 : 0;

    for (const token of tokens) {
      const matchScore = tokenScore(normalizedName, words, token);
      if (matchScore === null) return [];
      score += matchScore;
    }

    return [{ item, score }];
  });

  return ranked
    .sort((left, right) => right.score - left.score || left.item.name.localeCompare(right.item.name, undefined, { numeric: true }))
    .map(result => result.item);
}
