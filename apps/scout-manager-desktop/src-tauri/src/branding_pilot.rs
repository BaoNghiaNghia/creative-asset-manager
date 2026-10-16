//! Manual Amazon/Etsy pilot attached to the existing Scout Manager.
//! It is NOT part of Run automation and never auto-restarts.
use serde::Serialize;
use std::path::Path;
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
                #[cfg(windows)]
                {self.job.take();}
                let final_state=if exit.success(){
                    match self.action{
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
            Ok(None)=>{},
            Err(_)=>{
                self.child.take();
                #[cfg(windows)]
                {self.job.take();}
                self.action=None;
                self.state="Process error".into();
                self.last_error=Some("Unable to check Branding DNA process.".into());
            }
        }
    }
    pub fn status(&mut self,repo:&Path) -> BrandPilotStatus{
        self.check_status();
        let root=repo.join("apps/branding-dna-worker");
        BrandPilotStatus{
            state:self.state.clone(),
            pid:self.child.as_ref().map(Child::id),
            version:"0.2.0",
            configured:root.join("config/pilot.local.json").is_file(),
            setup_available:root.join("INSTALL_TOOL.cmd").is_file() && root.join("START_BROWSER_PILOT.cmd").is_file(),
            running:self.child.is_some(),
            last_error:self.last_error.clone(),
        }
    }
    pub fn action(&mut self, repo:&Path, log_root:&Path, command:&str)->Result<(),String>{
        self.check_status();
        if command=="stop"{
            self.stop();
            return Ok(());
        }
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
        if command=="run" && !root.join("config/pilot.local.json").is_file(){
            return Err("Configure config/pilot.local.json before running a shop pilot.".into());
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
        pilot.stop();
        pilot.stop();
        assert_eq!(pilot.state,"Stopped");
    }
}
