/** Per-shop durable progress for explicitly approved local browser pilots.
 * SQLite lives beside Windows reports, never on CAM VPS. No image bytes,
 * cookies, auth tokens or unverified Best Seller claims are stored here.
 */
import {DatabaseSync} from "node:sqlite";
import {createHash} from "node:crypto";
import {existsSync,lstatSync,mkdirSync,readdirSync,readFileSync} from "node:fs";
import {basename,join,resolve} from "node:path";

const MAX_KEYS=10000;
const MAX_REPORTS_TO_RECONCILE=2000;
const MAX_REPORT_BYTES=262144;
const sha=text=>createHash("sha256").update(text).digest("hex");

function safeReportName(value){
  return typeof value==="string"
    && /^branding-dna-[0-9]{8}-[0-9]{9}Z-[a-f0-9]{8}\.json$/.test(value)
    && basename(value)===value;
}
function verifiedObservation(item,platform){
  return item && typeof item==="object"
    && item.source_kind==="browser_dom_observation"
    && item.source_verification==="unverified"
    && typeof item.title==="string" && item.title.trim().length>0
    && !item.error_code
    && (platform==="amazon" ? /^amazon:[A-Z0-9]{10}$/ : /^etsy:[0-9]{1,20}$/)
      .test(item.listing_key??"");
}
function safeReport(manifest,shopHash,platform){
  return manifest && typeof manifest==="object"
    && ["preview_completed","needs_operator"].includes(manifest.status)
    && manifest.shop_url_sha256===shopHash && manifest.platform===platform
    && manifest.scan_enabled===false && manifest.evidence_committed===false
    && manifest.rights_verified===false && manifest.sales_verified===false
    && manifest.mode==="local_browser_dom_only"
    && Array.isArray(manifest.items) && manifest.items.length<=5;
}
function requireLocalStateDir(reportDir){
  const dir=resolve(reportDir);
  if(!existsSync(dir) || lstatSync(dir).isSymbolicLink() || !lstatSync(dir).isDirectory())
    throw new Error("report_directory_untrusted");
  const state=join(dir,".bdna-local-progress");
  if(existsSync(state) && (lstatSync(state).isSymbolicLink() || !lstatSync(state).isDirectory()))
    throw new Error("progress_directory_untrusted");
  mkdirSync(state,{recursive:true,mode:0o700});
  return state;
}

export function openShopProgress(reportDir,shop){
  if(!shop || !["amazon","etsy"].includes(shop.platform) || typeof shop.url!=="string")
    throw new Error("invalid_progress_shop");
  const shopHash=sha(shop.url),stateDir=requireLocalStateDir(reportDir);
  const path=join(stateDir,shop.platform+"-"+shopHash.slice(0,32)+".sqlite");
  if(existsSync(path) && lstatSync(path).isSymbolicLink())
    throw new Error("progress_db_symlink_forbidden");
  const db=new DatabaseSync(path,{open:true});
  try{
    db.exec("PRAGMA busy_timeout=5000; PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;");
    db.exec("CREATE TABLE IF NOT EXISTS observed_products("+
      "listing_key TEXT PRIMARY KEY NOT NULL, report_filename TEXT NOT NULL,"+
      "report_sha256 TEXT NOT NULL, observed_at TEXT NOT NULL);"+
      "CREATE TABLE IF NOT EXISTS accepted_reports("+
      "filename TEXT PRIMARY KEY NOT NULL, sha256 TEXT NOT NULL);");
    const reports=db.prepare("SELECT sha256 FROM accepted_reports WHERE filename=?");
    const reportInsert=db.prepare("INSERT INTO accepted_reports(filename,sha256) VALUES(?,?)");
    const productInsert=db.prepare("INSERT OR IGNORE INTO observed_products(listing_key,report_filename,report_sha256,observed_at) VALUES(?,?,?,?)");
    const productCount=db.prepare("SELECT COUNT(*) AS total FROM observed_products");
    const keys=db.prepare("SELECT listing_key FROM observed_products ORDER BY listing_key LIMIT ?");
    function record(filename,bytes,manifest){
      if(!safeReportName(filename) || !safeReport(manifest,shopHash,shop.platform))
        throw new Error("progress_report_not_trusted");
      if(!Buffer.isBuffer(bytes) || bytes.length>MAX_REPORT_BYTES)
        throw new Error("progress_report_too_large");
      const digest=sha(bytes);
      const prior=reports.get(filename);
      if(prior && prior.sha256!==digest)throw new Error("progress_report_immutable_violation");
      const accepted=manifest.items.filter(item=>verifiedObservation(item,shop.platform));
      db.exec("BEGIN IMMEDIATE");
      try{
        if(!prior)reportInsert.run(filename,digest);
        for(const item of accepted)
          productInsert.run(item.listing_key,filename,digest,manifest.observed_at??"");
        db.exec("COMMIT");
      }catch(error){db.exec("ROLLBACK");throw error}
      return accepted.length;
    }
    return {
      count(){return productCount.get().total;},
      completedKeys(){
        if(productCount.get().total>MAX_KEYS)throw new Error("local_progress_capacity_exceeded");
        return keys.all(MAX_KEYS).map(row=>row.listing_key);
      },
      record,
      reconcile(){
        // Crash between immutable report write and DB commit: rebuild without
        // visiting the shop again. Never trust a report from another shop.
        const names=readdirSync(reportDir)
          .filter(name=>safeReportName(name)).sort();
        if(names.length>MAX_REPORTS_TO_RECONCILE)
          throw new Error("too_many_local_reports_for_recovery");
        let restored=0;
        for(const name of names){
          const filename=join(reportDir,name);
          if(lstatSync(filename).isSymbolicLink() || !lstatSync(filename).isFile())
            throw new Error("progress_report_symlink_or_not_file");
          if(lstatSync(filename).size>MAX_REPORT_BYTES)
            throw new Error("progress_report_too_large");
          const bytes=readFileSync(filename);
          let manifest;
          try{manifest=JSON.parse(bytes.toString("utf8"))}catch{throw new Error("invalid_local_report_json")}
          // Reports for another shop are expected in legacy shared report folders.
          if(manifest?.shop_url_sha256!==shopHash || manifest?.platform!==shop.platform)continue;
          restored+=record(name,bytes,manifest);
        }
        return {restored,completed:this.count()};
      },
      close(){db.close();},
    };
  }catch(error){db.close();throw error}
}
