//! Manual review handoff for shortlisted Branding DNA pilot observations.
//! Export produces a local, unverified review packet; it cannot call CAM APIs,
//! create server evidence, claim usage rights, or schedule marketplace scanning.
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use crate::branding_pilot::{list_local_reports, load_config};
use crate::branding_pilot_review::list_reviews;

const MAX_CANDIDATES:usize=40;

#[derive(Serialize)]
#[serde(rename_all="camelCase")]
struct HandoffCandidate {
    platform: String,
    listing_key: String,
    title: String,
    image_reference_count: u64,
    report_file: String,
    report_sha256: String,
    item_index: u8,
    operator_note: String,
    source_kind: &'static str,
    source_verification: &'static str,
    rights_state: &'static str,
    training_rights_state: &'static str,
    sales_state: &'static str,
    required_verifications: [&'static str; 4],
}
#[derive(Serialize)]
#[serde(rename_all="camelCase")]
struct HandoffManifest {
    schema_version: &'static str,
    purpose: &'static str,
    platform: String,
    shop_url_sha256: String,
    generated_at_unix_ms: u128,
    candidate_count: usize,
    source_mode: &'static str,
    candidates: Vec<HandoffCandidate>,
    manual_source_verification_required: bool,
    rights_verified: bool,
    training_use_approved: bool,
    cam_import_eligible: bool,
    evidence_committed: bool,
    scan_enabled: bool,
}
#[derive(Serialize)]
#[serde(rename_all="camelCase")]
pub struct HandoffResult {
    pub output_path: String,
    pub candidate_count: usize,
    pub status: &'static str,
}

fn digest(text:&str)->String {
    format!("{:x}",Sha256::digest(text.as_bytes()))
}

fn destination_dir(local_data:&Path,shop_hash:&str)->Result<PathBuf,String>{
    if !local_data.is_absolute() || shop_hash.len()!=64
        || !shop_hash.bytes().all(|b|b.is_ascii_hexdigit()){
        return Err("Invalid local export location.".into());
    }
    let directory=local_data.join("CreativeAssetManager/BrandingDNA/handoffs").join(&shop_hash[..24]);
    if directory.exists(){
        let meta=fs::symlink_metadata(&directory).map_err(|_|"Cannot inspect handoff folder.")?;
        if meta.file_type().is_symlink() || !meta.is_dir(){
            return Err("Unsafe handoff folder.".into());
        }
    }
    Ok(directory)
}

fn prepare_packet(repo:&Path,local_data:&Path)->Result<HandoffManifest,String>{
    let config=load_config(repo)?.ok_or("Select a Branding DNA shop first.")?;
    let reports=list_local_reports(repo)?;
    let review_records=list_reviews(repo,local_data,&config.shop_url)?;
    let mut candidates=Vec::new();
    for review in review_records {
        let Some(report)=reports.iter().find(|r|
            r.file_name==review.file_name && r.report_sha256==review.report_sha256) else{continue};
        for choice in review.decisions.iter().filter(|c|c.decision=="picked"){
            if report.status!="preview_completed"{
                return Err("Blocked pilot cannot be handed off.".into());
            }
            let Some(item)=report.items.get(choice.item_index as usize) else{
                return Err("Review source has changed. Refresh before export.".into());
            };
            if item.listing_key.is_empty() || item.listing_key!=choice.listing_key
                || item.title.trim().chars().count()<2 || item.error_code.is_some(){
                return Err("Incomplete source listing cannot be handed off.".into());
            }
            if candidates.len()>=MAX_CANDIDATES {
                return Err("Too many candidates in one handoff packet.".into());
            }
            candidates.push(HandoffCandidate{
                platform:config.platform.clone(),
                listing_key:item.listing_key.clone(),
                title:item.title.clone(),
                image_reference_count:item.image_count,
                report_file:report.file_name.clone(),
                report_sha256:report.report_sha256.clone(),
                item_index:choice.item_index,
                operator_note:choice.note.clone(),
                source_kind:"browser_dom_observation",
                source_verification:"unverified",
                rights_state:"unknown",
                training_rights_state:"unknown",
                sales_state:"unknown",
                required_verifications:[
                    "confirm_product_and_authoritative_export",
                    "confirm_marketplace_access_authorization",
                    "confirm_image_and_artwork_usage_rights",
                    "confirm_media_lineage_and_original_files",
                ],
            });
        }
    }
    if candidates.is_empty(){
        return Err("No valid Pick decisions. Pick one or more completed observations first.".into());
    }
    let now=SystemTime::now().duration_since(UNIX_EPOCH)
        .map_err(|_|"System clock unavailable.")?.as_millis();
    Ok(HandoffManifest{
        schema_version:"brand-dna-local-handoff/v1",
        purpose:"manual_source_and_rights_verification_only",
        platform:config.platform,
        shop_url_sha256:digest(&config.shop_url),
        generated_at_unix_ms:now,
        candidate_count:candidates.len(),
        source_mode:"metadata_only_unverified_browser_pilot",
        candidates,
        manual_source_verification_required:true,
        rights_verified:false,
        training_use_approved:false,
        cam_import_eligible:false,
        evidence_committed:false,
        scan_enabled:false,
    })
}

/// Can be called only on a deliberate UI click. Does not access the network.
pub fn prepare_local_handoff(repo:&Path,local_data:&Path)->Result<HandoffResult,String>{
    let packet=prepare_packet(repo,local_data)?;
    let dir=destination_dir(local_data,&packet.shop_url_sha256)?;
    fs::create_dir_all(&dir).map_err(|_|"Cannot create local handoff folder.")?;
    if fs::symlink_metadata(&dir).map_err(|_|"Cannot inspect handoff folder.")?.file_type().is_symlink(){
        return Err("Unsafe local handoff folder.".into());
    }
    let canonical=serde_json::to_vec_pretty(&packet)
        .map_err(|_|"Cannot serialize review handoff.")?;
    let hash=format!("{:x}",Sha256::digest(&canonical));
    let filename=format!("handoff-{}-{}.json",packet.generated_at_unix_ms,&hash[..16]);
    let output=dir.join(filename);
    let mut file=OpenOptions::new().create_new(true).write(true).open(&output)
        .map_err(|_|"Cannot create unique handoff file.")?;
    if let Err(error)=file.write_all(&canonical).and_then(|_|file.write_all(b"\n")).and_then(|_|file.sync_all()){
        let _=fs::remove_file(&output);
        return Err(format!("Cannot finish handoff packet: {error}"));
    }
    Ok(HandoffResult{
        output_path:output.to_string_lossy().into_owned(),
        candidate_count:packet.candidate_count,
        status:"ready_for_manual_verification_not_cam_import",
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::branding_pilot::BrandPilot;
    use crate::branding_pilot_review::set_review;
    use serde_json::{json, Value};

    fn fixture()->(PathBuf,PathBuf,PathBuf,String,String){
        let timestamp=SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos();
        let root=std::env::temp_dir().join(format!("brand-handoff-test-{}-{timestamp}",std::process::id()));
        let repo=root.join("repo");
        let local=root.join("local");
        fs::create_dir_all(repo.join("apps/branding-dna-worker/config")).unwrap();
        fs::create_dir_all(&local).unwrap();
        let mut agent=BrandPilot::new();
        let shop=agent.save_shop(&repo,&local,"etsy","https://www.etsy.com/shop/VerifiableCaps",3,false).unwrap();
        fs::create_dir_all(&shop.report_dir).unwrap();
        let report_file="branding-dna-20261016-130000Z-1234abcd.json".to_owned();
        let observation=json!({
            "platform":"etsy",
            "shop_url_sha256":digest(&shop.shop_url),
            "status":"preview_completed",
            "observed_at":"2026-10-16T13:00:00Z",
            "scan_enabled":false,
            "evidence_committed":false,
            "items":[{"listing_key":"etsy:444123","title":"Everyday Cap","image_count":2}]
        });
        fs::write(Path::new(&shop.report_dir).join(&report_file),
            serde_json::to_vec(&observation).unwrap()).unwrap();
        let hash=list_local_reports(&repo).unwrap()[0].report_sha256.clone();
        (root,repo,local,report_file,hash)
    }

    #[test]
    fn exports_only_picked_items_and_never_claims_rights(){
        let (root,repo,local,file,hash)=fixture();
        assert!(prepare_local_handoff(&repo,&local).is_err());
        let shop="https://www.etsy.com/shop/VerifiableCaps";
        set_review(&repo,&local,shop,&file,&hash,0,"picked","Check origin and license").unwrap();
        let result=prepare_local_handoff(&repo,&local).unwrap();
        assert_eq!(result.candidate_count,1);
        let payload:Value=serde_json::from_slice(&fs::read(result.output_path).unwrap()).unwrap();
        assert_eq!(payload["camImportEligible"],false);
        assert_eq!(payload["rightsVerified"],false);
        assert_eq!(payload["trainingUseApproved"],false);
        assert_eq!(payload["evidenceCommitted"],false);
        assert_eq!(payload["scanEnabled"],false);
        assert_eq!(payload["candidates"][0]["operatorNote"],"Check origin and license");
        assert_eq!(payload["candidates"][0]["listingKey"],"etsy:444123");
        assert!(payload.get("tenant_id").is_none());
        assert!(payload["candidates"][0].get("image_url").is_none());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn fails_closed_on_changed_report_or_shop(){
        let (root,repo,local,file,hash)=fixture();
        let shop="https://www.etsy.com/shop/VerifiableCaps";
        set_review(&repo,&local,shop,&file,&hash,0,"picked","").unwrap();
        let cfg=load_config(&repo).unwrap().unwrap();
        let report_path=Path::new(&cfg.report_dir).join(&file);
        let mut changed:Value=serde_json::from_slice(&fs::read(&report_path).unwrap()).unwrap();
        changed["items"][0]["title"]="Changed title".into();
        fs::write(report_path,serde_json::to_vec(&changed).unwrap()).unwrap();
        assert!(prepare_local_handoff(&repo,&local).is_err());
        let mut agent=BrandPilot::new();
        agent.save_shop(&repo,&local,"etsy","https://www.etsy.com/shop/DifferentCaps",2,false).unwrap();
        assert!(prepare_local_handoff(&repo,&local).is_err());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn rejects_blocked_pilot_even_when_review_was_previously_picked(){
        let (root,repo,local,file,hash)=fixture();
        let shop="https://www.etsy.com/shop/VerifiableCaps";
        set_review(&repo,&local,shop,&file,&hash,0,"picked","").unwrap();
        let cfg=load_config(&repo).unwrap().unwrap();
        let report_path=Path::new(&cfg.report_dir).join(&file);
        let mut changed:Value=serde_json::from_slice(&fs::read(&report_path).unwrap()).unwrap();
        changed["status"]="needs_operator".into();
        fs::write(report_path,serde_json::to_vec(&changed).unwrap()).unwrap();
        assert!(prepare_local_handoff(&repo,&local).is_err());
        fs::remove_dir_all(root).unwrap();
    }
}
