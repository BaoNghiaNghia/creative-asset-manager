export const REFERENCE_LIFESTYLE_SEARCH_QUERIES = [
  "baseball cap selfie candid natural light",
  "casual woman baseball cap selfie",
  "casual man baseball cap lifestyle photo",
  "outdoor candid person wearing cap",
  "coffee shop baseball cap candid portrait",
  "car selfie baseball cap natural light",
  "corduroy cap casual lifestyle portrait",
  "authentic candid lifestyle portrait natural light",
  "casual family candid lifestyle photo",
  "everyday casual portrait at home",
] as const;

export const referenceLifestyleSearchQueries = (): string[] =>
  [...REFERENCE_LIFESTYLE_SEARCH_QUERIES];
