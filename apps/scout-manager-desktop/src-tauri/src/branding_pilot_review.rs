//! Local-only, source-revision-bound shortlist decisions for Branding DNA DOM pilots.
//! A "pick" only means selected for manual evidence/rights verification.
//! These files are never dispatched, promoted to verified evidence, or uploaded.
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::fs;
use std::path::{Path, PathBuf};

use crate::branding_pilot::{list_local_reports, load_config, PilotReportCard};

#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all="camelCase", deny_unknown_fields)]
pub struct ReviewChoice {
    pub item_index: u8,
    pub listing_key: String,
    pub decision: String,
    pub note: String,
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all="camelCase", deny_unknown_fields)]
pub struct ReviewRecord {
    pub file_name: String,
    pub report_sha256: String,
    pub shop_url_sha256: String,
    pub decisions: Vec<ReviewChoice>,
    pub scope: String,
}
fn require_active_shop(repo:&Path,shop_url:&str)->Result<(),String>{
    let active=load_config(repo)?.ok_or("No active Branding DNA shop selected.")?;
    if active.shop_url!=shop_url{return Err("Shop changed. Refresh the local reports.".into())}
    Ok(())
}
fn hex_digest(s:&str)->String {
    format!("{:x}",Sha256::digest(s.as_bytes()))
}
fn review_dir(local_data:&Path,shop_url:&str)->Result<PathBuf,String> {
    if !local_data.is_absolute(){return Err("LOCALAPPDATA must be an absolute path.".into())}
    let digest=hex_digest(shop_url);
    let dir=local_data.join("CreativeAssetManager/BrandingDNA/reviews").join(&digest[..24]);
    if dir.exists(){
        let meta=fs::symlink_metadata(&dir).map_err(|_|"Cannot inspect local review directory.")?;
        if !meta.is_dir() || meta.file_type().is_symlink(){
            return Err("Unsafe local review directory.".into());
        }
    }
    Ok(dir)
}
fn review_path(local_data:&Path,shop_url:&str,report_file:&str)->Result<PathBuf,String> {
    if !report_file.starts_with("branding-dna-")
        || !report_file.ends_with(".json") || report_file.len()>110
        || !report_file.bytes().all(|b|b.is_ascii_alphanumeric()||b==b'-'||b==b'.'){
        return Err("Invalid pilot report identifier.".into());
    }
    let digest=hex_digest(report_file);
    Ok(review_dir(local_data,shop_url)?.join(format!("report-{}.review.json",&digest[..32])))
}
fn load_review(path:&Path)->Result<Option<ReviewRecord>,String> {
    if !path.exists(){return Ok(None)}
    let meta=fs::symlink_metadata(path).map_err(|_|"Cannot inspect local review file.")?;
    if meta.file_type().is_symlink() || !meta.is_file() || meta.len()>16384{
        return Err("Unsafe local review file.".into());
    }
    let bytes=fs::read(path).map_err(|_|"Cannot read local review file.")?;
    let record:ReviewRecord=serde_json::from_slice(&bytes)
        .map_err(|_|"Corrupt local review file. Check the saved review before continuing.")?;
    if record.scope!="shortlist_unverified"
        || record.decisions.len()>5
        || record.decisions.iter().any(|d|
            !matches!(d.decision.as_str(),"picked"|"rejected"|"unreviewed")
            || d.note.chars().count()>200
            || d.note.chars().any(char::is_control)
            || d.listing_key.chars().count()>60){
        return Err("Invalid local review content.".into());
    }
    Ok(Some(record))
}
fn same_report(record:&ReviewRecord, report:&PilotReportCard,shop_url:&str)->bool {
    record.file_name==report.file_name && record.report_sha256==report.report_sha256
        && record.shop_url_sha256==hex_digest(shop_url)
}
pub fn list_reviews(repo:&Path,local_data:&Path,shop_url:&str)->Result<Vec<ReviewRecord>,String>{
    require_active_shop(repo,shop_url)?;
    // The active shop and report list are resolved by native code, never a
    // user-supplied path, preventing selection of arbitrary local files.
    let reports=list_local_reports(repo)?;
    let mut results=Vec::new();
    for report in reports {
        let path=review_path(local_data,shop_url,&report.file_name)?;
        if let Some(record)=load_review(&path)? {
            if same_report(&record,&report,shop_url) {
                // Bind every decision to the exact listing that was reviewed.
                if record.decisions.iter().all(|d|
                    report.items.get(d.item_index as usize)
                        .is_some_and(|item|item.listing_key==d.listing_key)){
                    results.push(record);
                }
            }
        }
    }
    Ok(results)
}
pub fn set_review(repo:&Path,local_data:&Path,shop_url:&str,
    report_file:&str,source_sha:&str,item_index:u8,decision:&str,note:&str)
    ->Result<ReviewRecord,String>{
    require_active_shop(repo,shop_url)?;
    if !matches!(decision,"picked"|"rejected"|"unreviewed"){
        return Err("Choose Pick, Reject or Unreviewed.".into());
    }
    if note.chars().count()>200 || note.chars().any(char::is_control){
        return Err("Review note must be one line, up to 200 characters.".into());
    }
    let report=list_local_reports(repo)?.into_iter()
        .find(|r|r.file_name==report_file).ok_or("Pilot report not found for active shop.")?;
    if report.report_sha256!=source_sha {
        return Err("Pilot report was changed. Refresh the report before reviewing.".into());
    }
    if decision=="picked" && report.status!="preview_completed"{
        return Err("Blocked or incomplete pilot reports cannot be shortlisted.".into());
    }
    let item=report.items.get(item_index as usize)
        .ok_or("Invalid pilot product index.")?;
    if item.listing_key.is_empty() || item.error_code.is_some() {
        return Err("Product needs a valid listing key and complete observation before review.".into());
    }
    let dir=review_dir(local_data,shop_url)?;
    fs::create_dir_all(&dir).map_err(|_|"Cannot create local review directory.")?;
    if fs::symlink_metadata(&dir).map_err(|_|"Cannot inspect local review directory.")?.file_type().is_symlink(){
        return Err("Unsafe local review directory.".into());
    }
    let dest=review_path(local_data,shop_url,report_file)?;
    let mut record=match load_review(&dest)? {
        Some(existing)=>{
            if !same_report(&existing,&report,shop_url){
                return Err("Existing review belongs to another source revision. Resolve it manually.".into());
            }
            existing
        }
        None=>ReviewRecord{
            file_name:report_file.into(),
            report_sha256:source_sha.into(),
            shop_url_sha256:hex_digest(shop_url),
            decisions:Vec::new(),
            scope:"shortlist_unverified".into(),
        }
    };
    record.decisions.retain(|d|d.item_index!=item_index);
    if decision!="unreviewed"{
        record.decisions.push(ReviewChoice{
            item_index,listing_key:item.listing_key.clone(),
            decision:decision.into(),note:note.trim().into(),
        });
    }
    record.decisions.sort_by_key(|d|d.item_index);
    let contents=serde_json::to_vec_pretty(&record).map_err(|_|"Cannot serialize review.")?;
    let stage=dir.join(format!("{}.{}.pending",hex_digest(report_file),std::process::id()));
    fs::write(&stage,&contents).map_err(|_|"Cannot stage local review.")?;
    let prior=dest.with_extension("previous.json");
    if dest.exists(){
        if prior.exists() && fs::symlink_metadata(&prior).map_err(|_|"Cannot inspect review backup.")?.file_type().is_symlink(){
            let _=fs::remove_file(&stage);
            return Err("Unsafe local review backup.".into());
        }
        if let Err(e)=fs::copy(&dest,&prior).and_then(|_|fs::remove_file(&dest)){
            let _=fs::remove_file(&stage);
            return Err(format!("Cannot back up previous review: {e}"));
        }
    }
    if let Err(e)=fs::rename(&stage,&dest){
        if prior.is_file() && !dest.exists(){let _=fs::copy(&prior,&dest);}
        let _=fs::remove_file(&stage);
        return Err(format!("Cannot save local review: {e}"));
    }
    Ok(record)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::branding_pilot::{BrandPilot, PilotShopConfig};
    use serde_json::json;

    fn fixture() -> (PathBuf,PathBuf,PathBuf,String,String){
        let nonce=std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
        let root=std::env::temp_dir().join(format!("brand-review-test-{}-{nonce}",std::process::id()));
        let repo=root.join("repo");
        let local=root.join("local");
        fs::create_dir_all(repo.join("apps/branding-dna-worker/config")).unwrap();
        fs::create_dir_all(&local).unwrap();
        let mut agent=BrandPilot::new();
        let view=agent.save_shop(&repo,&local,"etsy","https://www.etsy.com/shop/ReviewCaps",3,false).unwrap();
        let conf=PilotShopConfig{
            platform:view.platform.clone(),shop_url:view.shop_url.clone(),
            approval_file:view.approval_file,profile_dir:view.profile_dir,
            report_dir:view.report_dir.clone(),max_products:view.max_products,
            minimum_delay_ms:5000,maximum_delay_ms:None,
            interaction_mode:None,headless:view.headless,
        };
        let dir=Path::new(&view.report_dir);
        fs::create_dir_all(dir).unwrap();
        let file_name="branding-dna-20261016-120000Z-c0ffee42.json".to_string();
        let body=json!({
            "platform":"etsy","shop_url_sha256":hex_digest(&conf.shop_url),
            "scan_enabled":false,"evidence_committed":false,
            "status":"preview_completed","observed_at":"2026-10-16T12:00:00Z",
            "items":[{"listing_key":"etsy:12345678","title":"Canvas Cap","image_count":3}]
        });
        fs::write(dir.join(&file_name),serde_json::to_vec(&body).unwrap()).unwrap();
        let sha=list_local_reports(&repo).unwrap()[0].report_sha256.clone();
        (root,repo,local,file_name,sha)
    }
    #[test]
    fn shortlist_persists_and_reset_uses_exact_report_revision(){
        let (root,repo,local,file,sha)=fixture();
        let shop="https://www.etsy.com/shop/ReviewCaps";
        assert!(list_reviews(&repo,&local,shop).unwrap().is_empty());
        let r=set_review(&repo,&local,shop,&file,&sha,0,"picked","Verify product rights").unwrap();
        assert_eq!(r.decisions[0].decision,"picked");
        let loaded=list_reviews(&repo,&local,shop).unwrap();
        assert_eq!(loaded[0].decisions[0].note,"Verify product rights");
        let new=set_review(&repo,&local,shop,&file,&sha,0,"rejected","Not suitable").unwrap();
        assert_eq!(new.decisions[0].decision,"rejected");
        assert!(set_review(&repo,&local,shop,&file,&sha,0,"approved","").is_err());
        assert!(set_review(&repo,&local,shop,&file,&sha,0,"picked","line\ninjection").is_err());
        assert!(set_review(&repo,&local,shop,&file,&sha,3,"picked","").is_err());
        let empty=set_review(&repo,&local,shop,&file,&sha,0,"unreviewed","").unwrap();
        assert!(empty.decisions.is_empty());
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn blocked_pilot_cannot_be_picked_even_via_native_command(){
        let (root,repo,local,file,_)=fixture();
        let conf=load_config(&repo).unwrap().unwrap();
        let path=PathBuf::from(&conf.report_dir).join(&file);
        let mut doc:serde_json::Value=serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
        doc["status"]="needs_operator".into();
        doc["reason"]="captcha".into();
        fs::write(&path,serde_json::to_vec(&doc).unwrap()).unwrap();
        let sha=list_local_reports(&repo).unwrap()[0].report_sha256.clone();
        let shop="https://www.etsy.com/shop/ReviewCaps";
        assert!(set_review(&repo,&local,shop,&file,&sha,0,"picked","").is_err());
        assert!(set_review(&repo,&local,shop,&file,&sha,0,"rejected","Blocked").is_ok());
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn modified_report_does_not_inherit_shortlist(){
        let (root,repo,local,file,sha)=fixture();
        let shop="https://www.etsy.com/shop/ReviewCaps";
        set_review(&repo,&local,shop,&file,&sha,0,"picked","Original review").unwrap();
        let conf=load_config(&repo).unwrap().unwrap();
        let path=PathBuf::from(&conf.report_dir).join(&file);
        let mut doc:serde_json::Value=serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
        doc["items"][0]["title"]="Updated content".into();
        fs::write(&path,serde_json::to_vec(&doc).unwrap()).unwrap();
        assert!(list_reviews(&repo,&local,shop).unwrap().is_empty());
        let newsha=list_local_reports(&repo).unwrap()[0].report_sha256.clone();
        assert_ne!(newsha,sha);
        assert!(set_review(&repo,&local,shop,&file,&sha,0,"picked","").is_err());
        assert!(set_review(&repo,&local,shop,&file,&newsha,0,"picked","").is_err());
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn stale_report_and_wrong_shop_fail_closed(){
        let (root,repo,local,file,sha)=fixture();
        let shop="https://www.etsy.com/shop/ReviewCaps";
        assert!(set_review(&repo,&local,shop,&file,"000000",0,"picked","").is_err());
        assert!(set_review(&repo,&local,shop,"../../secret.json",&sha,0,"picked","").is_err());
        let dir=PathBuf::from(list_local_reports(&repo).unwrap()[0].file_name.clone());
        assert!(!dir.is_absolute());
        // The wrong-shop caller cannot resolve the current shop review directory.
        assert!(list_reviews(&repo,&local,"https://www.etsy.com/shop/OtherCaps").is_err());
        assert!(set_review(&repo,&local,"https://www.etsy.com/shop/OtherCaps",
            &file,&sha,0,"picked","").is_err());
        fs::remove_dir_all(root).unwrap();
    }
}
