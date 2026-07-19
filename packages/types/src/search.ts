export interface ExtractedMetadataValue {
  path: string;
  originalValue: string;
  valueType: "string" | "number" | "boolean";
}

export interface NormalizedMetadataValue {
  path: string;
  originalValue: string;
  normalizedValue: string;
  tokens: string[];
  numbers: string[];
  phrases: string[];
}

export interface SearchProjection {
  searchText: string;
  searchTerms: string[];
  normalizedTerms: string[];
  phrases: string[];
  numbers: string[];
  facets: Record<string, string[]>;
  pathValues: Array<{
    path: string;
    value: string;
  }>;
}

export interface SearchProjectionBuildResult {
  projection: SearchProjection;
  projectionVersion: string;
  queryConfig: {
    boostPaths: Record<string, number>;
  };
}
