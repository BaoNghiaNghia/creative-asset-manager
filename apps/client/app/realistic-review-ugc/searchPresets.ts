export const REFERENCE_LIFESTYLE_SEARCH_QUERIES = [
  "man wearing baseball cap candid phone photo",
  "man wearing hat casual outdoor lifestyle",
  "woman wearing bucket hat candid phone photo",
  "woman wearing hat casual lifestyle natural light",
  "couple wearing hats candid outdoor phone photo",
  "couple wearing caps casual lifestyle natural light",
  "family wearing hats candid outdoor natural light",
  "family wearing caps casual phone photo",
  "friends wearing hats candid smartphone photo",
  "friends wearing caps outdoor casual lifestyle",
] as const;

export const referenceLifestyleSearchQueries = (): string[] =>
  [...REFERENCE_LIFESTYLE_SEARCH_QUERIES];
