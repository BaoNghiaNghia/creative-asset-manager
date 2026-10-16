import { useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { Check, FileSearch, RefreshCw, RotateCcw, X } from "lucide-react";

type Observation = {listingKey:string;title:string;imageCount:number;errorCode:string|null};
type PilotReport = {
  fileName:string;reportSha256:string;status:string;observedAt:string;
  observedCount:number;reason:string|null;items:Observation[];
};
type ReviewChoice = {itemIndex:number;listingKey:string;decision:string;note:string};
type ReviewRecord = {
  fileName:string;reportSha256:string;shopUrlSha256:string;
  decisions:ReviewChoice[];scope:string;
};

/** Local shortlist only: picks do not grant rights or promote evidence to CAM. */
export function BrandingDnaReports({nativeAvailable, shopUrl, scanRunning}:{
  nativeAvailable:boolean;shopUrl:string;scanRunning:boolean;
}){
  const [reports,setReports]=useState<PilotReport[]>([]);
  const [reviews,setReviews]=useState<ReviewRecord[]>([]);
  const [loadedShop,setLoadedShop]=useState("");
  const [loading,setLoading]=useState(false);
  const [saving,setSaving]=useState("");
  const [error,setError]=useState("");
  const [notes,setNotes]=useState<Record<string,string>>({});
  const selectedShop=useRef(shopUrl);
  selectedShop.current=shopUrl;
  const refresh=useCallback(async()=>{
    if(!nativeAvailable||!shopUrl){
      setReports([]);setReviews([]);setLoadedShop("");return;
    }
    setLoading(true);setError("");
    try{
      const [found,chosen]=await Promise.all([
        invoke<PilotReport[]>("branding_pilot_reports"),
        invoke<ReviewRecord[]>("branding_pilot_review_list"),
      ]);
      if(selectedShop.current!==shopUrl)return;
      setReports(found);setReviews(chosen);setLoadedShop(shopUrl);
    }catch(e){
      if(selectedShop.current!==shopUrl)return;
      setError(typeof e==="string"?e:e instanceof Error?e.message:"Unable to read local pilot reviews");
      setReports([]);setReviews([]);setLoadedShop(shopUrl);
    }finally{
      if(selectedShop.current===shopUrl)setLoading(false);
    }
  },[nativeAvailable,shopUrl]);
  useEffect(()=>{void refresh()},[refresh,scanRunning]);
  const setDecision=async(report:PilotReport,itemIndex:number,decision:"picked"|"rejected"|"unreviewed")=>{
    const key=report.fileName+":"+report.reportSha256+":"+itemIndex;
    const note=notes[key]??"";
    if(decision==="picked" && report.status!=="preview_completed")return;
    setSaving(key);setError("");
    try{
      await invoke("branding_pilot_review_set",{
        reportFile:report.fileName,reportSha256:report.reportSha256,
        itemIndex,decision,note:decision==="unreviewed"?"":note,
      });
      await refresh();
    }catch(e){
      setError(typeof e==="string"?e:e instanceof Error?e.message:"Unable to save local review");
    }finally{setSaving("")}
  };
  if(!shopUrl)return null;
  const visible=loadedShop===shopUrl?reports:[];
  return <section className="branding-reports" aria-label="Local Branding DNA pilot results">
    <div className="branding-reports-header">
      <div><h3><FileSearch size={16}/> Local pilot results & review</h3>
        <p>Pick / Reject shortlist · source-hash bound · rights unverified · not uploaded to CAM</p></div>
      <button type="button" className="button secondary slim" disabled={!nativeAvailable||loading||Boolean(saving)}
        onClick={()=>void refresh()}><RefreshCw size={14}/> Refresh reports</button>
    </div>
    {error&&<div className="branding-pilot-error" role="alert">{error}</div>}
    {!loading&&!error&&visible.length===0&&<p className="branding-report-empty">No saved pilot results for this shop. Configure authorization and run a limited pilot to create a local report.</p>}
    <div className="branding-report-list">
      {visible.map((report,index)=>{
        const review=reviews.find(entry=>entry.fileName===report.fileName && entry.reportSha256===report.reportSha256);
        const picked=review?.decisions.filter(d=>d.decision==="picked").length??0;
        const rejected=review?.decisions.filter(d=>d.decision==="rejected").length??0;
        return <details key={report.fileName} className="branding-report-entry" open={index===0}>
          <summary>
            <strong>{report.status==="needs_operator"?"Needs operator":"Preview completed"}</strong>
            <span>{report.observedCount} observed · {picked} picked · {rejected} rejected · {report.observedAt?new Date(report.observedAt).toLocaleString():"time unavailable"}</span>
          </summary>
          {report.reason&&<p className="branding-report-warning">Reason: {report.reason}</p>}
          <div className="branding-report-items">
            {report.items.length===0?<p className="branding-report-empty">No product details were observed.</p>:
              report.items.map((item,itemIndex)=>{
                const current=review?.decisions.find(d=>d.itemIndex===itemIndex);
                const key=report.fileName+":"+report.reportSha256+":"+itemIndex;
                const invalid=!item.listingKey||Boolean(item.errorCode);
                const controlsDisabled=!nativeAvailable||scanRunning||loading||Boolean(saving)||invalid;
                return <div key={item.listingKey+"-"+itemIndex} className="branding-report-item">
                  <div className="branding-review-title">
                    <strong>{item.title||item.listingKey||"Untitled product"}</strong>
                    <span>{current?.decision==="picked"?"Picked · verify rights":current?.decision==="rejected"?"Rejected":"Not reviewed"}</span>
                  </div>
                  <span>{item.listingKey} · {item.imageCount} image references (hash only)</span>
                  {item.errorCode&&<span className="branding-report-warning">{item.errorCode} · review unavailable</span>}
                  <div className="branding-review-actions">
                    <input type="text" value={notes[key]??current?.note??""} maxLength={200}
                      placeholder="Optional review note (local only)"
                      aria-label={"Review note for "+(item.title||item.listingKey)}
                      disabled={controlsDisabled}
                      onChange={e=>setNotes(old=>({...old,[key]:e.target.value}))}/>
                    <button type="button" className="button secondary slim" disabled={controlsDisabled||report.status!=="preview_completed"}
                      aria-label="Pick for manual verification" onClick={()=>void setDecision(report,itemIndex,"picked")}>
                      <Check size={14}/> Pick</button>
                    <button type="button" className="button secondary slim" disabled={controlsDisabled}
                      aria-label="Reject observation" onClick={()=>void setDecision(report,itemIndex,"rejected")}>
                      <X size={14}/> Reject</button>
                    <button type="button" className="button secondary slim" disabled={controlsDisabled||!current}
                      aria-label="Reset review status" onClick={()=>void setDecision(report,itemIndex,"unreviewed")}>
                      <RotateCcw size={14}/> Reset</button>
                  </div>
                </div>;
              })}
          </div>
          <small>{report.fileName} · Reviews stored locally, not rights verification or training approval</small>
        </details>;
      })}
    </div>
  </section>;
}
