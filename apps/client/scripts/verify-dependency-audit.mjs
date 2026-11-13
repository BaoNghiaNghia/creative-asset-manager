// Consume npm audit JSON and block the configured minimum advisory severity.
// Default (high) preserves the original gate; CI now enforces moderate or above.
const levels = ["low", "moderate", "high", "critical"];
const argument = process.argv.slice(2);
const thresholdArg = argument.find(value => value.startsWith("--minimum-severity="));
const threshold = thresholdArg?.slice("--minimum-severity=".length) ?? "high";
if (argument.length > (thresholdArg ? 1 : 0) || !levels.includes(threshold)) {
  console.error("Unknown audit gate arguments; use --minimum-severity=low|moderate|high|critical.");
  process.exit(1);
}
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
const minIndex = levels.indexOf(threshold);
const blocked = Object.values(audit.vulnerabilities || {})
  .filter(item => levels.indexOf(item.severity) >= minIndex);
const blockingCount = levels.slice(minIndex).reduce((sum, level) => sum + (Number(count[level]) || 0), 0);
if (blocked.length || blockingCount) {
  for (const item of blocked) console.error("BLOCK: " + item.name + " (" + item.severity + ")");
  process.exitCode = 1;
} else {
  console.log("PASS: no npm advisories at or above " + threshold + " severity.");
}
