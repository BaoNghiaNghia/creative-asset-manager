import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { FileSearch, RefreshCw } from "lucide-react";

type Observation = {listingKey:string;title:string;imageCount:number;errorCode:string|null};
type PilotReport = {
  fileName:string;status:string;observedAt:string;observedCount:number;
  reason:string|null;items:Observation[];
};

/** Read-only summary of locally saved, shop-matched and unverified DOM evidence. */
export function BrandingDnaReports({nativeAvailable, shopUrl, scanRunning}:{
  nativeAvailable:boolean;shopUrl:string;scanRunning:boolean;
}){
  const [reports,setReports]=useState<PilotReport[]>([]);
  const [loading,setLoading]=useState(false);
  const [error,setError]=useState("");
  const refresh=useCallback(async()=>{
    if(!nativeAvailable||!shopUrl){setReports([]);return;}
    setLoading(true);setError("");
    try{setReports(await invoke<PilotReport[]>("branding_pilot_reports"))}
    catch(e){setError(typeof e==="string"?e:e instanceof Error?e.message:"Unable to read local reports");setReports([])}
    finally{setLoading(false)}
  },[nativeAvailable,shopUrl]);
  useEffect(()=>{void refresh()},[refresh,scanRunning]);
  if(!shopUrl)return null;
  return <section className="branding-reports" aria-label="Local Branding DNA pilot results">
    <div className="branding-reports-header">
      <div><h3><FileSearch size={16}/> Local pilot results</h3>
        <p>Read-only DOM observations · not verified brand DNA · not uploaded to CAM</p></div>
      <button type="button" className="button secondary slim" disabled={!nativeAvailable||loading}
        onClick={()=>void refresh()}><RefreshCw size={14}/> Refresh reports</button>
    </div>
    {error&&<div className="branding-pilot-error" role="alert">{error}</div>}
    {!loading&&!error&&reports.length===0&&<p className="branding-report-empty">No saved pilot results for this shop. Configure authorization and run a limited pilot to create a local report.</p>}
    <div className="branding-report-list">
      {reports.map((report,i)=><details key={report.fileName} className="branding-report-entry" open={i===0}>
        <summary>
          <strong>{report.status==="needs_operator"?"Needs operator":"Preview completed"}</strong>
          <span>{report.observedCount} observed · {report.observedAt?new Date(report.observedAt).toLocaleString():"time unavailable"}</span>
        </summary>
        {report.reason&&<p className="branding-report-warning">Reason: {report.reason}</p>}
        <div className="branding-report-items">
          {report.items.length===0?<p className="branding-report-empty">No product details were observed.</p>:
            report.items.map((item,i)=><div key={item.listingKey+"-"+i} className="branding-report-item">
              <strong>{item.title||item.listingKey||"Untitled product"}</strong>
              <span>{item.listingKey} · {item.imageCount} image references (hash only)</span>
              {item.errorCode&&<span className="branding-report-warning">{item.errorCode}</span>}
            </div>)}
        </div>
        <small>{report.fileName} · Local only · Copyright/rights and sales not verified</small>
      </details>)}
    </div>
  </section>;
}
