/** Offline readiness check; does NOT open pages, start jobs or send credentials. */
import {existsSync} from "node:fs";
import {pathToFileURL, fileURLToPath} from "node:url";
import {resolve} from "node:path";

export function checkRuntime({nodeVersion=process.versions.node,executable=""}={}){
  const [major,minor]=nodeVersion.split(".").map(Number);
  if(!(major>22 || major===22&&minor>=13))throw new Error("Node.js 22.13+ required");
  if(!executable || !existsSync(executable))throw new Error("Chromium not installed in dedicated browsers path");
  return {nodeVersion,chromiumReady:true};
}

if(process.argv[1] && import.meta.url===pathToFileURL(resolve(process.argv[1])).href){
  try{
    const {chromium}=await import("playwright");
    const binary=chromium.executablePath();
    const ready=checkRuntime({executable:binary});
    process.stdout.write("Branding DNA browser runtime ready; Node "+ready.nodeVersion+
      "; Chromium installed. Production dispatcher remains OFF.\n");
  }catch(error){
    process.stderr.write("Worker setup incomplete: "+(error instanceof Error?error.message:"unknown")+"\n");
    process.exitCode=2;
  }
}
