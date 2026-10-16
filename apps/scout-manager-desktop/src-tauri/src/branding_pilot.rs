//! Manual Amazon/Etsy pilot attached to the existing Scout Manager.
//! It is NOT part of Run automation and never auto-restarts.
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};

#[cfg(windows)]
use crate::winjob;

#[derive(Serialize)]
#[serde(rename_all="camelCase")]
pub struct BrandPilotStatus {
    pub state: String,
    pub pid: Option<u32>,
    pub version: &'static str,
    pub configured: bool,
    pub setup_available: bool,
    pub running: bool,
    pub last_error: Option<String>,
    pub approval_file_present: bool,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PilotShopConfig {
    pub platform: String,
    pub shop_url: String,
    pub approval_file: String,
    pub profile_dir: String,
    pub report_dir: String,
    pub max_products: u8,
    pub minimum_delay_ms: u32,
    pub headless: bool,
}
#[derive(Serialize)]
#[serde(rename_all="camelCase")]
pub struct ShopConfigView {
    pub configured: bool,
    pub platform: String,
    pub shop_url: String,
    pub max_products: u8,
    pub headless: bool,
    pub approval_file: String,
    pub approval_file_present: bool,
    pub profile_dir: String,
    pub report_dir: String,
}
fn config_path(repo:&Path)->PathBuf{
    repo.join("apps/branding-dna-worker/config/pilot.local.json")
}
fn normalize_shop_url(platform:&str,raw:&str)->Result<String,String>{
    let parsed=reqwest::Url::parse(raw.trim()).map_err(|_|"Invalid shop URL.".to_string())?;
    if parsed.scheme()!="https" || !parsed.username().is_empty() || parsed.password().is_some()
        || parsed.port().is_some() || parsed.query().is_some() || parsed.fragment().is_some(){
        return Err("Only HTTPS public shop URLs without query, fragment or credentials are supported.".into());
    }
    let host=parsed.host_str().ok_or("Shop host is required.")?.to_ascii_lowercase();
    let path=parsed.path();
    // Keep native preflight no broader than the actual Node browser allowlist.
    if !path.is_ascii() || path.contains('%') || path.contains('\\') || path.contains('@')
        || path.contains("..") {return Err("Unsafe or unsupported shop path.".into());}
    let parts=path.trim_matches('/').split('/').collect::<Vec<_>>();
    let valid_segment=|s:&str| ->bool {
        (2..=120).contains(&s.len())
            && s.bytes().all(|b| b.is_ascii_alphanumeric() || b==b'_' || b==b'-' || b==b'.')
    };
    match platform {
        "etsy"=>{
            if !["www.etsy.com","etsy.com"].contains(&host.as_str())
                || parts.len()!=2 || parts[0]!="shop" || !valid_segment(parts[1]){
                return Err("Provide an Etsy storefront URL like https://www.etsy.com/shop/ShopName.".into());
            }
            Ok(format!("https://www.etsy.com/shop/{}",parts[1]))
        }
        "amazon"=>{
            let allowed=["www.amazon.com","www.amazon.co.uk","www.amazon.ca",
                "www.amazon.de","www.amazon.fr","www.amazon.es","www.amazon.it","www.amazon.co.jp"];
            if !allowed.contains(&host.as_str()) || parts.len()<2 || parts.len()>8
                || parts[0]!="stores" || !parts[1..].iter().all(|s|valid_segment(s)){
                return Err("Provide a supported Amazon storefront URL under /stores/.".into());
            }
            Ok(format!("https://{host}/stores/{}",parts[1..].join("/")))
        }
        _=>Err("Platform must be Amazon or Etsy.".into())
    }
}
fn fingerprint(text:&str)->String{
    // A stable local folder identifier, not a security token.
    let mut hash=0xcbf29ce484222325_u64;
    for b in text.bytes(){hash ^= u64::from(b); hash=hash.wrapping_mul(0x100000001b3);}
    format!("{hash:016x}")
}
fn pilot_config_for(
    platform:&str,shop_url:&str,max_products:u8,headless:bool,
    local_data:&Path,existing:Option<&PilotShopConfig>,
)->Result<PilotShopConfig,String>{
    let url=normalize_shop_url(platform,shop_url)?;
    if !(1..=5).contains(&max_products){return Err("Pilot product limit must be 1–5.".into())}
    if !local_data.is_absolute() {return Err("Local app data must be an absolute path.".into())}
    if let Some(old)=existing {
        if old.platform==platform && old.shop_url==url {
            return Ok(PilotShopConfig{platform:platform.into(),shop_url:url,max_products,headless,
                approval_file:old.approval_file.clone(),
                profile_dir:old.profile_dir.clone(),
                report_dir:old.report_dir.clone(),
                minimum_delay_ms:old.minimum_delay_ms});
        }
    }
    let id=format!("{platform}-{}",fingerprint(&url));
    let base=local_data.join("CreativeAssetManager/BrandingDNA");
    Ok(PilotShopConfig{
        platform:platform.into(),shop_url:url,
        approval_file:base.join("secrets").join(format!("{id}.approval.json")).to_string_lossy().to_string(),
        profile_dir:base.join("profiles").join(&id).to_string_lossy().to_string(),
        report_dir:base.join("reports").join(id).to_string_lossy().to_string(),
        max_products,minimum_delay_ms:5000,headless,
    })
}
fn load_config(repo:&Path)->Result<Option<PilotShopConfig>,String>{
    let p=config_path(repo);
    if !p.exists(){return Ok(None)}
    if p.symlink_metadata().map_err(|_|"Cannot inspect shop configuration.")?.file_type().is_symlink(){
        return Err("Shop configuration symlink is not allowed.".into());
    }
    let bytes=fs::read(&p).map_err(|_|"Cannot read shop configuration.")?;
    if bytes.len()>8192{return Err("Shop configuration is too large.".into())}
    serde_json::from_slice::<PilotShopConfig>(&bytes).map(Some).map_err(|_|"Invalid shop configuration; review your local JSON.".into())
}
fn view(config:Option<&PilotShopConfig>)->ShopConfigView{
    match config {
        None=>ShopConfigView{configured:false,platform:"amazon".into(),shop_url:String::new(),
            max_products:3,headless:false,approval_file:String::new(),
            approval_file_present:false,profile_dir:String::new(),report_dir:String::new()},
        Some(c)=>ShopConfigView{configured:true,platform:c.platform.clone(),shop_url:c.shop_url.clone(),
            max_products:c.max_products,headless:c.headless,approval_file:c.approval_file.clone(),
            approval_file_present:Path::new(&c.approval_file).is_file(),
            profile_dir:c.profile_dir.clone(),report_dir:c.report_dir.clone()},
    }
}
fn save_config(repo:&Path,config:&PilotShopConfig)->Result<(),String>{
    let dir=repo.join("apps/branding-dna-worker/config");
    if !dir.is_dir(){return Err("Branding DNA config directory is missing.".into())}
    let dest=config_path(repo);
    if dest.exists() && dest.symlink_metadata().map_err(|_|"Cannot inspect shop configuration.")?.file_type().is_symlink(){
        return Err("Shop configuration symlink is not allowed.".into());
    }
    let bytes=serde_json::to_vec_pretty(config).map_err(|_|"Cannot serialize shop configuration.")?;
    let pending=dir.join(format!("pilot.local.{}.pending",std::process::id()));
    fs::write(&pending,[bytes.as_slice(),b"\n"].concat()).map_err(|_|"Cannot prepare local shop configuration.")?;
    // Preserve the previous on-disk file until the new content is ready.
    let prior=dir.join("pilot.local.previous.json");
    if prior.exists() && prior.symlink_metadata().map_err(|_|"Cannot inspect config backup.")?.file_type().is_symlink(){
        let _=fs::remove_file(&pending);
        return Err("Shop config backup symlink is forbidden.".into());
    }
    if dest.is_file(){
        if let Err(error)=fs::copy(&dest,&prior){
            let _=fs::remove_file(&pending);
            return Err(format!("Cannot back up existing config: {error}"));
        }
        if let Err(error)=fs::remove_file(&dest) {
            let _=fs::remove_file(&pending);
            return Err(format!("Cannot replace existing config: {error}"));
        }
    }
    if let Err(error)=fs::rename(&pending,&dest){
        if prior.is_file() {let _=fs::copy(&prior,&dest);}
        let _=fs::remove_file(&pending);
        return Err(format!("Cannot install new shop config: {error}"));
    }
    Ok(())
}
fn approval_template(config:&PilotShopConfig)->serde_json::Value{
    serde_json::json!({
        "platform":config.platform,
        "shop_url":config.shop_url,
        "browser_access_approved":false,
        "source_rights_reviewed":false,
        "platform_terms_reviewed":false,
        "robots_policy_reviewed":false,
        "approved_by":"",
        "approved_at":"",
        "amazon_agent_policy_reviewed":false,
        "etsy_written_authorization":false,
        "etsy_authorization_reference":""
    })
}
fn ensure_default_deny_approval(config:&PilotShopConfig)->Result<PathBuf,String>{
    let path=PathBuf::from(&config.approval_file);
    if !path.is_absolute(){return Err("Approval file path must be absolute.".into())}
    let dir=path.parent().ok_or("Approval directory missing.")?;
    fs::create_dir_all(dir).map_err(|_|"Cannot create approval directory.")?;
    if path.exists(){
        if path.symlink_metadata().map_err(|_|"Cannot inspect approval file.")?.file_type().is_symlink(){
            return Err("Approval file symlink is forbidden.".into());
        }
    } else {
        let text=serde_json::to_string_pretty(&approval_template(config)).map_err(|_|"Cannot prepare approval template.")?;
        let mut file=fs::OpenOptions::new().write(true).create_new(true).open(&path)
            .map_err(|_|"Cannot create approval file.")?;
        use std::io::Write;
        file.write_all(text.as_bytes()).map_err(|_|"Cannot write approval file.")?;
    }
    Ok(path)
}

#[derive(Serialize)]
#[serde(rename_all="camelCase")]
pub struct PilotObservation {
    pub listing_key: String,
    pub title: String,
    pub image_count: u64,
    pub error_code: Option<String>,
}
#[derive(Serialize)]
#[serde(rename_all="camelCase")]
pub struct PilotReportCard {
    pub file_name: String,
    pub status: String,
    pub observed_at: String,
    pub observed_count: usize,
    pub reason: Option<String>,
    pub items: Vec<PilotObservation>,
}
// Read-only and strictly local: no external calls, no raw source/image URLs,
// no secrets, no acceptance of another shop's report.
pub fn list_local_reports(repo:&Path)->Result<Vec<PilotReportCard>,String>{
    let Some(config)=load_config(repo)? else{return Ok(Vec::new())};
    let dir=Path::new(&config.report_dir);
    if !dir.exists(){return Ok(Vec::new())}
    let metadata=fs::symlink_metadata(dir).map_err(|_|"Cannot inspect local report directory.")?;
    if metadata.file_type().is_symlink() || !metadata.is_dir(){
        return Err("Unsafe local report directory.".into());
    }
    let shop_sha=format!("{:x}",Sha256::digest(config.shop_url.as_bytes()));
    let mut files=Vec::new();
    for entry in fs::read_dir(dir).map_err(|_|"Cannot read local report directory.")?.take(2000){
        let entry=entry.map_err(|_|"Cannot inspect local report entry.")?;
        let name=entry.file_name().to_string_lossy().into_owned();
        if !name.starts_with("branding-dna-") || !name.ends_with(".json")
            || name.len()>110 || !name.bytes().all(|b|b.is_ascii_alphanumeric() || b==b'-' || b==b'.'){
            continue;
        }
        let meta=entry.metadata().map_err(|_|"Cannot inspect local report.")?;
        if !meta.is_file() || meta.len()>1024*1024 || meta.len()<10 || entry.path().symlink_metadata()
            .map_err(|_|"Cannot inspect local report.")?.file_type().is_symlink(){continue}
        files.push((name,entry.path()));
    }
    files.sort_by(|a,b|b.0.cmp(&a.0));
    let mut reports=Vec::new();
    for (file_name,path) in files {
        if reports.len()>=8{break}
        let Ok(content)=fs::read(&path) else{continue};
        let Ok(doc)=serde_json::from_slice::<serde_json::Value>(&content) else{continue};
        if doc["shop_url_sha256"].as_str()!=Some(shop_sha.as_str())
            || doc["platform"].as_str()!=Some(config.platform.as_str())
            || doc["scan_enabled"].as_bool()!=Some(false)
            || doc["evidence_committed"].as_bool()!=Some(false)
            || !matches!(doc["status"].as_str(),Some("preview_completed"|"needs_operator")){
            continue;
        }
        let items: Vec<PilotObservation>=doc["items"].as_array().map(|arr|arr.iter().take(5).map(|item|{
            PilotObservation{
                listing_key:item["listing_key"].as_str().unwrap_or("").chars().take(60).collect(),
                title:item["title"].as_str().unwrap_or("").chars().take(180).collect(),
                image_count:item["image_count"].as_u64().unwrap_or(0).min(12),
                error_code:item["error_code"].as_str().map(|s|s.chars().take(100).collect()),
            }
        }).collect()).unwrap_or_default();
        reports.push(PilotReportCard{
            file_name,
            status:doc["status"].as_str().unwrap_or("").to_string(),
            observed_at:doc["observed_at"].as_str().unwrap_or("").chars().take(40).collect(),
            observed_count:doc["observed_count"].as_u64().unwrap_or(items.len() as u64).min(5) as usize,
            reason:doc["reason"].as_str().map(|s|s.chars().take(100).collect()),
            items,
        });
    }
    Ok(reports)
}

pub struct BrandPilot {
    child: Option<Child>,
    #[cfg(windows)]
    job: Option<winjob::Job>,
    state: String,
    last_error: Option<String>,
    action: Option<&'static str>,
}
impl BrandPilot {
    pub fn new() -> Self {
        Self { child:None,
               #[cfg(windows)] job:None,
               state:"Not configured".into(),last_error:None,action:None }
    }
    fn check_status(&mut self){
        let Some(child)=self.child.as_mut() else {return};
        match child.try_wait(){
            Ok(Some(exit))=>{
                self.child.take();
                #[cfg(windows)]{self.job.take();}
                let final_state=if exit.success(){
                    match self.action {
                        Some("run") => "Pilot ended · review local report",
                        Some("install") => "Installed · configure shop",
                        Some("check") => "Ready",
                        _ => "Stopped",
                    }
                } else {"Needs attention"};
                self.state=final_state.to_owned();
                self.last_error=if exit.success(){None}else{
                    Some("Review Branding DNA live logs for the exit reason.".into())
                };
                self.action=None;
            }
            Ok(None)=>{}
            Err(_)=>{
                self.child.take();
                #[cfg(windows)]{self.job.take();}
                self.action=None;
                self.state="Process error".into();
                self.last_error=Some("Unable to check Branding DNA process.".into());
            }
        }
    }
    pub fn status(&mut self,repo:&Path)->BrandPilotStatus{
        self.check_status();
        let root=repo.join("apps/branding-dna-worker");
        let approval_file_present=load_config(repo).ok().flatten()
            .map(|c|Path::new(&c.approval_file).is_file()).unwrap_or(false);
        BrandPilotStatus{
            state:self.state.clone(),
            pid:self.child.as_ref().map(Child::id),
            version:"0.2.0",
            configured:root.join("config/pilot.local.json").is_file(),
            setup_available:root.join("INSTALL_TOOL.cmd").is_file() && root.join("START_BROWSER_PILOT.cmd").is_file(),
            running:self.child.is_some(),
            last_error:self.last_error.clone(),
            approval_file_present,
        }
    }
    pub fn load_shop(&mut self,repo:&Path)->Result<ShopConfigView,String>{
        self.check_status();
        load_config(repo).map(|c|view(c.as_ref()))
    }
    pub fn save_shop(&mut self,repo:&Path,local_data:&Path,
        platform:&str,shop_url:&str,max_products:u8,headless:bool)->Result<ShopConfigView,String>{
        self.check_status();
        if self.child.is_some(){return Err("Stop Branding DNA pilot before editing its shop.".into())}
        let old=load_config(repo)?;
        let cfg=pilot_config_for(platform,shop_url,max_products,headless,local_data,old.as_ref())?;
        save_config(repo,&cfg)?;
        self.state="Shop configured · approval required".into();
        self.last_error=None;
        Ok(view(Some(&cfg)))
    }
    pub fn prepare_approval(&mut self,repo:&Path)->Result<PathBuf,String>{
        self.check_status();
        if self.child.is_some(){return Err("Stop Branding DNA pilot before opening its approval.".into())}
        let cfg=load_config(repo)?.ok_or("Configure a shop first.")?;
        let path=ensure_default_deny_approval(&cfg)?;
        Ok(path)
    }
    pub fn action(&mut self, repo:&Path, log_root:&Path, command:&str)->Result<(),String>{
        self.check_status();
        if command=="stop"{self.stop();return Ok(())}
        if self.child.is_some(){
            return Err("Branding DNA action is already running. Stop it before starting another.".into());
        }
        let script=match command {
            "install"=>"INSTALL_TOOL.cmd",
            "check"=>"CHECK_TOOL.cmd",
            "run"=>"START_BROWSER_PILOT.cmd",
            _=>return Err("Unsupported Branding DNA action.".into()),
        };
        let root=repo.join("apps/branding-dna-worker");
        let script_path=root.join(script);
        if !script_path.is_file(){
            return Err("Branding DNA runtime missing from Scout checkout. Update source first.".into());
        }
        if command=="run" {
            let cfg=load_config(repo)?.ok_or("Configure a shop before running a browser pilot.")?;
            if !Path::new(&cfg.approval_file).is_file() {
                return Err("Missing authorization document. Create and review it before running.".into());
            }
        }
        let stdout=crate::open_log(&log_root.join("branding_dna.stdout.log"))?;
        let stderr=crate::open_log(&log_root.join("branding_dna.stderr.log"))?;
        let mut process=Command::new("cmd.exe");
        process.args(["/D","/C"])
            .arg(&script_path)
            .current_dir(&root)
            .stdin(Stdio::null())
            .stdout(Stdio::from(stdout))
            .stderr(Stdio::from(stderr));
        crate::hide_console(&mut process);
        let mut child=process.spawn().map_err(|_|"Could not start Branding DNA launcher.".to_string())?;
        #[cfg(windows)]
        {
            match winjob::Job::attach(&child){
                Ok(job)=>self.job=Some(job),
                Err(error)=>{
                    let _=child.kill();let _=child.wait();return Err(error);
                }
            }
        }
        self.action=match command{"install"=>Some("install"),"check"=>Some("check"),_=>Some("run")};
        self.state=match command{"install"=>"Installing Chromium","check"=>"Checking runtime",_=>"Pilot running"}.into();
        self.last_error=None;
        self.child=Some(child);
        Ok(())
    }
    pub fn stop(&mut self){
        if let Some(mut child)=self.child.take(){
            #[cfg(windows)]{self.job.take();}
            let _=child.kill();
            let _=child.try_wait();
        }
        self.action=None;
        self.state="Stopped".into();
        self.last_error=None;
    }
}
#[cfg(test)]
mod tests{
    use super::*;
    fn fake_root()->PathBuf{
        let nonce=std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
        let root=std::env::temp_dir().join(format!("brand-pilot-config-test-{}-{nonce}",
            std::process::id()));
        let _=fs::remove_dir_all(&root);
        fs::create_dir_all(root.join("repo/apps/branding-dna-worker/config")).unwrap();
        fs::create_dir_all(root.join("local")).unwrap();
        root
    }
    #[test]
    fn rejects_non_store_and_unsafe_urls(){
        let invalid=[
            ("etsy","https://www.etsy.com/listing/100"),
            ("etsy","https://www.etsy.com/shop/A?ref=bad"),
            ("amazon","https://amazon.com/stores/Brand"),
            ("amazon","https://www.amazon.com/stores/Brand%2fadmin"),
            ("amazon","https://www.amazon.com/stores/Brand/../../etc"),
            ("shopify","https://www.etsy.com/shop/Brand"),
        ];
        for (platform,url) in invalid{
            assert!(normalize_shop_url(platform,url).is_err(),"{platform}: {url}");
        }
        assert_eq!(normalize_shop_url("etsy","https://etsy.com/shop/MyStore/").unwrap(),
            "https://www.etsy.com/shop/MyStore");
        assert_eq!(normalize_shop_url("amazon","https://www.amazon.com/stores/MyCaps/page/XYZ").unwrap(),
            "https://www.amazon.com/stores/MyCaps/page/XYZ");
    }
    #[test]
    fn creates_isolated_profiles_and_default_deny_approval(){
        let root=fake_root();
        let repo=root.join("repo");
        let local=root.join("local");
        let mut agent=BrandPilot::new();
        let first=agent.save_shop(&repo,&local,"etsy","https://www.etsy.com/shop/TestHats",3,false).unwrap();
        assert!(first.configured);
        assert!(!first.approval_file_present);
        assert!(first.profile_dir.contains("BrandingDNA"));
        let p=agent.prepare_approval(&repo).unwrap();
        let body=fs::read_to_string(&p).unwrap();
        assert!(body.contains("\"browser_access_approved\": false"));
        assert!(body.contains("\"etsy_written_authorization\": false"));
        fs::write(&p,"preserve original operator authorization file").unwrap();
        let second=agent.save_shop(&repo,&local,"etsy","https://www.etsy.com/shop/TestHats",5,true).unwrap();
        assert_eq!(second.approval_file,first.approval_file);
        assert_eq!(second.profile_dir,first.profile_dir);
        assert_eq!(second.max_products,5);
        assert!(second.approval_file_present);
        let updated=agent.save_shop(&repo,&local,"etsy","https://www.etsy.com/shop/OtherHats",2,false).unwrap();
        assert_ne!(updated.profile_dir,first.profile_dir);
        assert_ne!(updated.approval_file,first.approval_file);
        assert!(!updated.approval_file_present);
        assert_eq!(fs::read_to_string(&p).unwrap(),"preserve original operator authorization file");
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn local_reports_are_read_only_and_isolated_by_shop_hash(){
        let root=fake_root();
        let repo=root.join("repo");
        let local=root.join("local");
        let mut agent=BrandPilot::new();
        let shop=agent.save_shop(&repo,&local,"etsy","https://www.etsy.com/shop/HappyCaps",3,false).unwrap();
        let dir=PathBuf::from(&shop.report_dir);
        fs::create_dir_all(&dir).unwrap();
        let hash=format!("{:x}",Sha256::digest(shop.shop_url.as_bytes()));
        let matching=serde_json::json!({
            "platform":"etsy","shop_url_sha256":hash,
            "status":"preview_completed","observed_at":"2026-10-16T12:00:00Z",
            "observed_count":1,"scan_enabled":false,"evidence_committed":false,
            "items":[{"listing_key":"etsy:12345678","title":"Cotton Cap","image_count":2}],
        });
        fs::write(dir.join("branding-dna-20261016-120000Z-abc12345.json"),
            serde_json::to_string(&matching).unwrap()).unwrap();
        let unrelated=serde_json::json!({
            "platform":"etsy","shop_url_sha256":"different_shop_hash",
            "status":"preview_completed","observed_at":"2026-10-16T13:00:00Z",
            "scan_enabled":false,"evidence_committed":false,
            "items":[{"title":"Hidden competitor","listing_key":"etsy:999"}],
        });
        fs::write(dir.join("branding-dna-20261016-130000Z-def23456.json"),
            serde_json::to_string(&unrelated).unwrap()).unwrap();
        let reports=list_local_reports(&repo).unwrap();
        assert_eq!(reports.len(),1);
        assert_eq!(reports[0].items[0].title,"Cotton Cap");
        assert_eq!(reports[0].observed_count,1);
        let other=agent.save_shop(&repo,&local,"etsy","https://www.etsy.com/shop/DifferentCaps",2,false).unwrap();
        assert_ne!(shop.report_dir,other.report_dir);
        assert!(list_local_reports(&repo).unwrap().is_empty());
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn brand_pilot_never_starts_with_pinterest(){
        let mut pilot=BrandPilot::new();
        let root=std::env::temp_dir().join("cam-brand-test-not-found");
        let status=pilot.status(&root);
        assert!(!status.running);
        assert!(!status.configured);
        assert!(!status.setup_available);
        assert!(pilot.action(&root,&root,"start").is_err());
        assert!(pilot.action(&root,&root,"run").is_err());
    }
    #[test]
    fn stop_is_idempotent(){
        let mut pilot=BrandPilot::new();
        pilot.stop();pilot.stop();
        assert_eq!(pilot.state,"Stopped");
    }
}
