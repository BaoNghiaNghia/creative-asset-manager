// Consume npm audit JSON and fail on high or critical advisories.
// Moderate TipTap 2 findings remain a separate editor-migration workstream.
let input = "";
for await (const chunk of process.stdin) input += chunk;
let audit;
try { audit = JSON.parse(input); } catch {
  console.error("Dependency audit returned invalid JSON.");
  process.exit(1);
}
if (audit.error || !audit.metadata?.vulnerabilities) {
  console.error("Dependency audit failed:", audit.error?.summary || "missing vulnerability counts");
  process.exit(1);
}
const count = audit.metadata.vulnerabilities;
console.log("Dependency audit: critical=" + (count.critical ?? 0) +
  ", high=" + (count.high ?? 0) + ", moderate=" + (count.moderate ?? 0) +
  ", low=" + (count.low ?? 0));
const serious = Object.values(audit.vulnerabilities || {})
  .filter(item => item.severity === "critical" || item.severity === "high");
if (serious.length || count.critical || count.high) {
  for (const item of serious) console.error("BLOCK: " + item.name + " (" + item.severity + ")");
  process.exitCode = 1;
} else {
  console.log("PASS: no high or critical npm advisories.");
}
