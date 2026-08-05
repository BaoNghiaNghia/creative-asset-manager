import type { ParsedQueryDebug, SearchCapabilities, SearchFacetBucket } from "../types";

type Props = {
  capabilities: SearchCapabilities;
  facets: Record<string, SearchFacetBucket[]>;
  selected: Record<string, string[]>;
  parsed: ParsedQueryDebug | null;
  onToggle: (name: string, value: string) => void;
};

export function SearchGuide({ capabilities }: Pick<Props, "capabilities">) {
  return <div className="search-guide">
    <button type="button" aria-describedby="search-guide-tooltip">
      <span aria-hidden="true">&#8984;</span>
      <b>Search guide</b>
      <small>Keywords, phrases and filters</small>
    </button>
    <div id="search-guide-tooltip" className="search-guide-content" role="tooltip" aria-label="Search syntax guide">
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
  </div>;
}

export function SearchControls({ facets, selected, parsed, onToggle }: Props) {
  return <div className="search-v2-controls">
    <div className="search-facet-groups" aria-label="Search filters">
      {Object.entries(facets).filter(([, buckets]) => buckets.length).map(([name, buckets]) => <fieldset className="search-facets" key={name}><legend>{name}</legend>{buckets.map(bucket => <label key={bucket.value}><input type="checkbox" checked={(selected[name] || []).includes(bucket.value)} onChange={() => onToggle(name, bucket.value)} />{bucket.value}<small>{bucket.count}</small></label>)}</fieldset>)}
    </div>
    {parsed && <details className="parsed-query-debug"><button type="button" aria-describedby="search-guide-tooltip">Parsed query debug</button><pre>{JSON.stringify(parsed, null, 2)}</pre></details>}
  </div>;
}
