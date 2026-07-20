import { useState } from "react";
import type { ParsedQueryDebug, SearchCapabilities, SearchFacetBucket } from "../types";

type Props = {
  capabilities: SearchCapabilities;
  facets: Record<string, SearchFacetBucket[]>;
  selected: Record<string, string[]>;
  parsed: ParsedQueryDebug | null;
  onToggle: (name: string, value: string) => void;
};

export function SearchV2Controls({ capabilities, facets, selected, parsed, onToggle }: Props) {
  const [help, setHelp] = useState(false);
  return <div className="search-v2-controls">
    <button className="search-help-button" type="button" onClick={() => setHelp(value => !value)} aria-expanded={help}>? Search syntax</button>
    {help && <div className="search-help" role="note"><b>Search examples</b><ul>{capabilities.examples.map(example => <li key={example}><code>{example}</code></li>)}</ul><p>Spaces use soft AND; commas require every term; quotes match a phrase; OR matches either side.</p></div>}
    {Object.entries(facets).filter(([, buckets]) => buckets.length).map(([name, buckets]) => <fieldset className="search-facets" key={name}><legend>{name}</legend>{buckets.map(bucket => <label key={bucket.value}><input type="checkbox" checked={(selected[name] || []).includes(bucket.value)} onChange={() => onToggle(name, bucket.value)} />{bucket.value}<small>{bucket.count}</small></label>)}</fieldset>)}
    {parsed && <details className="parsed-query-debug"><summary>Parsed query debug</summary><pre>{JSON.stringify(parsed, null, 2)}</pre></details>}
  </div>;
}
