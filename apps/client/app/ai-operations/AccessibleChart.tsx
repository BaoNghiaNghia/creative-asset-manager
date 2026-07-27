import type { ChartDatum } from "./presentation";

type Props = {
  title: string;
  description: string;
  data: ChartDatum[];
  valueLabel?: (value: number) => string;
};

const colors = ["#3769e8", "#d84a4a", "#8b5cf6", "#0f9f79"];

export function AccessibleChart({ title, description, data, valueLabel = String }: Props) {
  const series = [...new Set(data.flatMap(item => Object.keys(item.values)))];
  const maximum = Math.max(1, ...data.flatMap(item => Object.values(item.values)));
  const width = 640;
  const height = 210;
  const plotHeight = 145;
  const groupWidth = data.length ? width / data.length : width;
  const barWidth = Math.max(3, Math.min(24, (groupWidth - 12) / Math.max(1, series.length)));
  const isEmpty = data.length === 0;
  return <figure className={`ops-chart${isEmpty ? " is-empty" : ""}`} aria-labelledby={`chart-${slug(title)}`}>
    <div className="ops-chart-heading">
      <div><h3 id={`chart-${slug(title)}`}>{title}</h3><p>{description}</p></div>
      <span className="ops-chart-legend">{series.map((name, index) => <i key={name}><b style={{ background: colors[index % colors.length] }} />{name}</i>)}</span>
    </div>
    {isEmpty ? <div className="ops-chart-empty"><strong>No data in this period</strong><span>Try a wider date range or different filters.</span></div> : <>
      <svg role="img" aria-label={`${title}. ${description}`} viewBox={`0 0 ${width} ${height}`}>
        <line x1="0" x2={width} y1={plotHeight} y2={plotHeight} stroke="#d9e0e8" />
        {data.map((item, groupIndex) => <g key={item.label}>
          {series.map((name, seriesIndex) => {
            const value = item.values[name] || 0;
            const barHeight = value / maximum * (plotHeight - 12);
            const x = groupIndex * groupWidth + (groupWidth - barWidth * series.length) / 2 + seriesIndex * barWidth;
            return <rect key={name} x={x} y={plotHeight - barHeight} width={Math.max(2, barWidth - 2)} height={barHeight} rx="2" fill={colors[seriesIndex % colors.length]}>
              <title>{`${item.label}, ${name}: ${valueLabel(value)}`}</title>
            </rect>;
          })}
          {(data.length <= 12 || groupIndex % Math.ceil(data.length / 12) === 0) && <text
            x={groupIndex * groupWidth + groupWidth / 2} y={plotHeight + 19}
            textAnchor="middle" className="ops-chart-label"
          >{shortLabel(item.label)}</text>}
        </g>)}
      </svg>
      <details className="ops-chart-table">
        <summary>View chart data table</summary>
        <div className="ops-table-scroll"><table>
          <thead><tr><th>Category</th>{series.map(name => <th key={name}>{name}</th>)}</tr></thead>
          <tbody>{data.map(item => <tr key={item.label}>
            <th>{item.label}</th>{series.map(name => <td key={name}>{valueLabel(item.values[name] || 0)}</td>)}
          </tr>)}</tbody>
        </table></div>
      </details>
    </>}
    <figcaption className="sr-only">{description}</figcaption>
  </figure>;
}

function slug(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "-");
}

function shortLabel(value: string): string {
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    return new Date(`${value}T00:00:00Z`).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
  }
  const [provider, mode] = value.split(" \u00b7 ");
  if (mode) return `${provider.replace("Google ", "")} / ${mode}`;
  return value.length > 16 ? `${value.slice(0, 15)}...` : value;
}
