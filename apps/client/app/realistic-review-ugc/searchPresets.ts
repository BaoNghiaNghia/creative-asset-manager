export const REFERENCE_LIFESTYLE_SEARCH_QUERIES = [
  "baseball cap selfie iphone natural light",
  "baseball cap mirror selfie casual outfit",
  "woman wearing cap iphone selfie candid",
  "man wearing cap casual phone photo",
  "car selfie baseball cap natural light",
  "coffee shop selfie baseball cap phone photo",
  "corduroy cap selfie candid smartphone",
  "casual selfie iphone natural light",
  "candid phone photo at home",
  "casual family candid smartphone photo",
] as const;

export const referenceLifestyleSearchQueries = (): string[] =>
  [...REFERENCE_LIFESTYLE_SEARCH_QUERIES];