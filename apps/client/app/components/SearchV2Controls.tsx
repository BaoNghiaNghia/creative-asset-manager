import type { ParsedQueryDebug, SearchCapabilities, SearchFacetBucket } from "../types";

type Props = {
  capabilities: SearchCapabilities;
  facets: Record<string, SearchFacetBucket[]>;
  selected: Record<string, string[]>;
  parsed: ParsedQueryDebug | null;
  onToggle: (name: string, value: string) => void;
};

export function SearchV2Controls({ capabilities, facets, selected, parsed, onToggle }: Props) {
  return <div className="search-v2-controls">
    <details className="search-guide">
      <summary>
        <span aria-hidden="true">⌘</span>
        <b>Search guide</b>
        <small>Keywords, phrases and filters</small>
      </summary>
      <div className="search-guide-content" role="note" aria-label="Search syntax guide">
        <ul className="search-guide-examples">
          <li><code>cat mama</code><span>Matches both terms by relevance</span></li>
          <li><code>cat, mama</code><span>Requires every term</span></li>
          <li><code>"est 2015"</code><span>Matches an exact phrase</span></li>
          <li><code>cat OR dog</code><span>Matches either term</span></li>
          <li><code>subject:cat</code><span>Filters a metadata field</span></li>
        </ul>
        {capabilities.examples.length > 0 && <div className="search-guide-api-examples">
          <span>Try</span>
          {capabilities.examples.slice(0, 4).map(example => <code key={example}>{example}</code>)}
        </div>}
      </div>
    </details>
    <div className="search-facet-groups" aria-label="Search filters">
      {Object.entries(facets).filter(([, buckets]) => buckets.length).map(([name, buckets]) => <fieldset className="search-facets" key={name}><legend>{name}</legend>{buckets.map(bucket => <label key={bucket.value}><input type="checkbox" checked={(selected[name] || []).includes(bucket.value)} onChange={() => onToggle(name, bucket.value)} />{bucket.value}<small>{bucket.count}</small></label>)}</fieldset>)}
    </div>
    {parsed && <details className="parsed-query-debug"><summary>Parsed query debug</summary><pre>{JSON.stringify(parsed, null, 2)}</pre></details>}
  </div>;
}
