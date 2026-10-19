/** Purge only completed timestamp-named UI QA runs, never golden baselines.
 * Guarded entrypoint for the profile-matrix EXIT trap and manual cleanup.
 */
import path from "node:path";
import { cleanupRuns } from "./ui-qa-cleanup.mjs";

const output = process.argv[2] || ".ui-qa";
const resolved = path.resolve(output);
if (path.basename(resolved) !== ".ui-qa") {
  throw new Error("Refusing purge outside a .ui-qa directory");
}
await cleanupRuns(resolved, 0);
console.log("Purged completed ephemeral UI QA run folders:", resolved);
