export const REFERENCE_LIFESTYLE_SEARCH_QUERIES = [
  "authentic candid lifestyle portrait",
  "casual family candid lifestyle photo",
  "parent child outdoor candid photo",
  "dad child candid lifestyle photo",
  "casual man selfie natural light",
  "casual woman selfie natural light",
  "everyday casual portrait at home",
  "outdoor candid portrait natural light",
  "embroidered baseball cap casual selfie",
  "corduroy cap casual lifestyle portrait",
] as const;

export const referenceLifestyleSearchQueries = (): string[] =>
  [...REFERENCE_LIFESTYLE_SEARCH_QUERIES];
