import { useEffect, useRef, useState } from "react";
import type { ParsedQueryDebug, SearchCapabilities, SearchFacetBucket } from "../types";
import { DESIGN_TYPE_FILTER_KEY } from "../hooks/useSearchV3";

type Props = {
  capabilities: SearchCapabilities;
  facets: Record<string, SearchFacetBucket[]>;
  selected: Record<string, string[]>;
  parsed: ParsedQueryDebug | null;
  onToggle: (name: string, value: string) => void;
};

type CategoryFilterProps = Pick<Props, "selected"> & {
  onChange: (name: string, values: string[]) => void;
};

const categoryOptions = [
  { label: "All", values: [] },
  { label: "PetFull / PeopleFull / CarFull", values: ["petfull", "peoplefull", "carfull"] },
  { label: "PetFull", values: ["petfull"] },
  { label: "PetOutline", values: ["petoutline"] },
  { label: "PeopleFull", values: ["peoplefull"] },
  { label: "PeopleOutline", values: ["peopleoutline"] },
  { label: "CarFull", values: ["carfull"] },
  { label: "CarOutline", values: ["caroutline"] },
  { label: "ExistedDesign", values: ["existeddesign"] },
  { label: "Roman", values: ["roman"] },
  { label: "Handwriting", values: ["handwriting"] },
  { label: "Floral", values: ["floral"] },
  { label: "Neckline", values: ["neckline"] },
  { label: "Text", values: ["text"] },
  { label: "Other tags", values: ["other tags"] },
] as const;

export function SearchCategoryFilter({ selected, onChange }: CategoryFilterProps) {
  const current = selected[DESIGN_TYPE_FILTER_KEY] || [];
  const currentKey = [...current].sort().join("|");

  return <aside className="search-category-filter" aria-label="Filter image results by type">
    <header>
      <small>Filter</small>
      <h2>Design type</h2>
    </header>
    <div role="radiogroup" aria-label="Design type">
      {categoryOptions.map(option => {
        const values = [...option.values];
        const checked = values.length === 0 ? current.length === 0 : currentKey === values.slice().sort().join("|");
        return <label key={option.label} className={checked ? "active" : ""}>
          <input
            type="radio"
            name="search-design-type"
            checked={checked}
            onChange={() => onChange(DESIGN_TYPE_FILTER_KEY, values)}
          />
          <span>{option.label}</span>
        </label>;
      })}
    </div>
  </aside>;
}

const guideExamples = [
  ["hoodie cat", "T\u00ecm theo m\u1ee9c \u0111\u1ed9 li\u00ean quan."],
  ["hoodie, cat", "K\u1ebft qu\u1ea3 ph\u1ea3i ch\u1ee9a \u0111\u1ea7y \u0111\u1ee7 c\u00e1c t\u1eeb."],
  ['"est 2015"', "T\u00ecm ch\u00ednh x\u00e1c c\u1ee5m t\u1eeb."],
  ["cat OR dog", "K\u1ebft qu\u1ea3 ch\u1ee9a m\u1ed9t trong hai t\u1eeb."],
  ["subject:cat", "L\u1ecdc theo tr\u01b0\u1eddng metadata."],
] as const;

export function SearchGuide({ capabilities: _capabilities }: Pick<Props, "capabilities">) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);
  const popupId = "search-guide-popup";

  useEffect(() => {
    const onPointerDown = (event: PointerEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    const media = typeof window !== "undefined" && window.matchMedia ? window.matchMedia("(max-width: 1024px)") : null;
    const closeWhenCompact = () => { if (media?.matches) setOpen(false); };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    media?.addEventListener?.("change", closeWhenCompact);
    closeWhenCompact();
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
      media?.removeEventListener?.("change", closeWhenCompact);
    };
  }, []);

  return <div className="search-guide" ref={ref}>
    <button
      type="button"
      aria-expanded={open}
      aria-controls={popupId}
      aria-haspopup="dialog"
      onClick={() => setOpen(value => !value)}
    >
      <span aria-hidden="true">&#8984;</span>
      <b>{"H\u01b0\u1edbng d\u1eabn t\u00ecm ki\u1ebfm"}</b>
      <small>{"T\u1eeb kh\u00f3a, c\u1ee5m t\u1eeb v\u00e0 b\u1ed9 l\u1ecdc"}</small>
    </button>
    {open && <div id={popupId} className="search-guide-content" role="dialog" aria-label={"H\u01b0\u1edbng d\u1eabn t\u00ecm ki\u1ebfm"}>
      <h3>{"H\u01b0\u1edbng d\u1eabn t\u00ecm ki\u1ebfm"}</h3>
      <ul className="search-guide-examples">
        {guideExamples.map(([syntax, description]) => <li key={syntax}><code>{syntax}</code><span>{description}</span></li>)}
      </ul>
      <div className="search-guide-api-examples">
        <span>{"Th\u1eed:"}</span>
        <code>cat</code><code>hoodie cat</code><code>cat, embroidery</code><code>"est 2015"</code>
      </div>
    </div>}
  </div>;
}

export function SearchControls({ facets, selected, parsed, onToggle }: Props) {
  return <div className="search-v2-controls">
    <div className="search-facet-groups" aria-label="Search filters">
      {Object.entries(facets)
        .filter(([, buckets]) => buckets.length)
        .map(([name, buckets]) => (
          <fieldset className="search-facets" key={name}>
            <legend>{name}</legend>
            {buckets.map(bucket => (
              <label key={bucket.value}>
                <input
                  type="checkbox"
                  checked={(selected[name] || []).includes(bucket.value)}
                  onChange={() => onToggle(name, bucket.value)}
                />
                {bucket.value}
                <small>{bucket.count}</small>
              </label>
            ))}
          </fieldset>
        ))}
    </div>
    {parsed && (
      <details className="parsed-query-debug">
        <button type="button" aria-describedby="search-guide-tooltip">Parsed query debug</button>
        <pre>{JSON.stringify(parsed, null, 2)}</pre>
      </details>
    )}
  </div>;
}
