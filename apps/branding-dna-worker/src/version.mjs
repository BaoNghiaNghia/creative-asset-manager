/** Release provenance comes from the package shipped with this runtime. */
import {readFileSync} from "node:fs";

export const WORKER_VERSION=JSON.parse(
  readFileSync(new URL("../package.json",import.meta.url),"utf8")
).version;
