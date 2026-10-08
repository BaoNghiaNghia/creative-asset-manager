#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
use serde::Serialize;
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};
use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{Manager as _, WindowEvent};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct ModeInfo {
    mode: String,
    state: String,
    pid: Option<u32>,
    desired: bool,
    restarts: u32,
    last_error: Option<String>,
}
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct Dashboard {
    version: String,
    commit: String,
    update_state: String,
    paired: bool,
    updating: bool,
    controller_available: bool,
    modes: Vec<ModeInfo>,
}

struct Scout {
    name: &'static str,
    child: Option<Child>,
    #[cfg(windows)]
    job: Option<winjob::Job>,
    desired: bool,
    restarts: u32,
    started_at: Option<Instant>,
    next_try: Instant,
    state: String,
    last_error: Option<String>,
}
impl Scout {
    fn new(name: &'static str) -> Self {
        Self {
            name,
            child: None,
            #[cfg(windows)]
            job: None,
            desired: false,
            restarts: 0,
            started_at: None,
            next_try: Instant::now(),
            state: "Stopped".into(),
            last_error: None,
        }
    }
    fn info(&self) -> ModeInfo {
        ModeInfo {
            mode: self.name.into(),
            state: self.state.clone(),
            pid: self.child.as_ref().map(|p| p.id()),
            desired: self.desired,
            restarts: self.restarts,
            last_error: self.last_error.clone(),
        }
    }
}

struct Controller {
    repo: PathBuf,
    log_root: PathBuf,
    modes: Mutex<Vec<Scout>>,
    #[cfg(windows)]
    manager_lock: Option<winjob::ManagerLock>,
    update_state: Mutex<String>,
    updating: AtomicBool,
}
fn repo_candidate(path: &Path) -> bool {
    path.join("scripts/start_scout_auto_update.ps1").is_file()
        && path.join("apps/rrugc_scout/scout.py").is_file()
}
fn shell_friendly_path(path: PathBuf) -> PathBuf {
    // Windows canonicalize() prefixes paths with \\?\\. PowerShell 5.1
    // does not reliably populate $PSScriptRoot when invoked with that form.
    #[cfg(windows)]
    {
        let value = path.to_string_lossy();
        if let Some(unc) = value.strip_prefix(r"\\?\UNC\") {
            return PathBuf::from(format!(r"\\{}", unc));
        }
        if let Some(local) = value.strip_prefix(r"\\?\") {
            return PathBuf::from(local);
        }
    }
    path
}
fn locate_repo() -> Result<PathBuf, String> {
    let mut candidates = Vec::new();
    if let Some(v) = std::env::var_os("CAM_SCOUT_REPO_ROOT") {
        candidates.push(PathBuf::from(v));
    }
    if let Ok(dir) = std::env::current_dir() {
        candidates.extend(dir.ancestors().map(Path::to_path_buf));
    }
    if let Ok(exe) = std::env::current_exe() {
        candidates.extend(exe.ancestors().map(Path::to_path_buf));
    }
    candidates.push(PathBuf::from(r"D:\Bot_Tool_Auto_Game\scan_pinterest"));
    candidates.into_iter().find(|p| repo_candidate(p)).and_then(|p| p.canonicalize().ok()).map(shell_friendly_path)
        .ok_or_else(|| "Scout checkout not found. Set CAM_SCOUT_REPO_ROOT to the existing scan_pinterest folder.".into())
}
fn config_values(repo: &Path) -> Vec<(String, String)> {
    let text = fs::read_to_string(repo.join("scout.local.env")).unwrap_or_default();
    text.lines()
        .filter_map(|line| {
            let (key, value) = line.split_once('=')?;
            let key = key.trim();
            if key.starts_with('#') {
                return None;
            }
            Some((
                key.to_string(),
                value.trim().trim_matches('"').trim_matches('\'').into(),
            ))
        })
        .collect()
}
fn is_paired(repo: &Path) -> bool {
    let entries = config_values(repo);
    ["RRUGC_AGENT_ID", "RRUGC_SCOUT_TOKEN"]
        .iter()
        .all(|key| entries.iter().any(|(k, v)| k == key && !v.is_empty()))
}
fn execute_git(repo: &Path, args: &[&str]) -> Result<String, String> {
    let output = Command::new("git")
        .arg("-C")
        .arg(repo)
        .args(args)
        .output()
        .map_err(|_| "Git is not installed or unavailable.".to_string())?;
    if !output.status.success() {
        return Err("Git operation failed. Check your repository and network.".into());
    }
    Ok(String::from_utf8_lossy(&output.stdout).trim().to_string())
}
fn log_file(root: &Path, mode: &str) -> PathBuf {
    root.join(format!("{}.stdout.log", mode))
}
fn file_tail(path: &Path, lines: usize) -> String {
    let Ok(mut f) = File::open(path) else {
        return String::new();
    };
    let Ok(meta) = f.metadata() else {
        return String::new();
    };
    let length = meta.len();
    let start = length.saturating_sub(128 * 1024);
    if f.seek(SeekFrom::Start(start)).is_err() {
        return String::new();
    }
    let mut buf = Vec::new();
    if f.read_to_end(&mut buf).is_err() {
        return String::new();
    }
    let text = String::from_utf8_lossy(&buf);
    let mut pieces = text.lines().rev().take(lines).collect::<Vec<_>>();
    pieces.reverse();
    pieces.join("\n")
}
fn open_log(path: &Path) -> Result<File, String> {
    if path
        .metadata()
        .map(|m| m.len() > 12 * 1024 * 1024)
        .unwrap_or(false)
    {
        let archive = path.with_extension("previous.log");
        let _ = fs::remove_file(&archive);
        let _ = fs::rename(path, archive);
    }
    OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
        .map_err(|_| "Unable to write Scout diagnostic log.".into())
}
impl Controller {
    fn new() -> Result<Self, String> {
        let repo = locate_repo()?;
        let log_root = std::env::var_os("CAM_SCOUT_LOG_ROOT")
            .map(PathBuf::from)
            .unwrap_or_else(|| {
                std::env::var_os("LOCALAPPDATA")
                    .map(PathBuf::from)
                    .unwrap_or_else(std::env::temp_dir)
                    .join("CreativeAssetManager/RrugcScoutManager/logs")
            });
        fs::create_dir_all(&log_root)
            .map_err(|_| "Cannot initialize local Scout logs.".to_string())?;
        Ok(Self {
            repo,
            log_root,
            modes: Mutex::new(vec![Scout::new("review"), Scout::new("keyword")]),
            #[cfg(windows)]
            manager_lock: winjob::ManagerLock::acquire()?,
            update_state: Mutex::new("Checking every 60 seconds".into()),
            updating: AtomicBool::new(false),
        })
    }
    fn owns_manager_lock(&self) -> bool {
        #[cfg(windows)]
        {
            self.manager_lock.is_some()
        }
        #[cfg(not(windows))]
        {
            true
        }
    }
    fn require_manager_lock(&self) -> Result<(), String> {
        if self.owns_manager_lock() {
            Ok(())
        } else {
            Err("Another Scout Manager already controls this checkout. Close the legacy Manager before using Tauri.".into())
        }
    }
    fn lock_modes(&self) -> Result<std::sync::MutexGuard<'_, Vec<Scout>>, String> {
        self.modes
            .lock()
            .map_err(|_| "Scout controller unavailable".into())
    }
    fn launch(&self, scout: &mut Scout) -> Result<(), String> {
        if !is_paired(&self.repo) {
            scout.state = "Pairing required".into();
            return Err("Pairing required. Set your Scout Agent ID and token.".into());
        }
        if scout.child.is_some() {
            return Ok(());
        }
        let path = self.repo.join("scripts/start_scout_auto_update.ps1");
        let name = scout.name;
        let output = open_log(&log_file(&self.log_root, name))?;
        let errors = open_log(&self.log_root.join(format!("{}.stderr.log", name)))?;
        let mut process = Command::new("powershell.exe");
        process
            .args([
                "-NoLogo",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
            ])
            .arg(path)
            .arg("-SkipUpdate")
            .current_dir(&self.repo)
            .stdin(Stdio::null())
            .stdout(Stdio::from(output))
            .stderr(Stdio::from(errors));
        if name == "keyword" {
            process.arg("-KeywordMode");
        }
        #[cfg(windows)]
        process.creation_flags(0x08000000); // CREATE_NO_WINDOW: suppress duplicate CMD consoles
        let mut child = process
            .spawn()
            .map_err(|_| "Could not start Scout Python launcher.".to_string())?;
        #[cfg(windows)]
        {
            match winjob::Job::attach(&child) {
                Ok(job) => scout.job = Some(job),
                Err(error) => {
                    let _ = child.kill();
                    let _ = child.wait();
                    return Err(error);
                }
            }
        }
        scout.child = Some(child);
        scout.started_at = Some(Instant::now());
        scout.state = "Running".into();
        scout.last_error = None;
        Ok(())
    }
    fn terminate(&self, scout: &mut Scout) {
        if let Some(mut child) = scout.child.take() {
            #[cfg(windows)]
            {
                scout.job.take();
            } // closing the job handle kills the entire process tree
            let _ = child.kill();
            let _ = child.wait();
        }
        scout.state = "Stopped".into();
        scout.started_at = None;
    }
    fn control(&self, mode: &str, command: &str) -> Result<(), String> {
        self.require_manager_lock()?;
        if self.updating.load(Ordering::SeqCst) {
            return Err("Source update is in progress.".into());
        }
        let mut all = self.lock_modes()?;
        let scout = all
            .iter_mut()
            .find(|m| m.name == mode)
            .ok_or("Unknown Scout mode.")?;
        match command {
            "start" => {
                scout.desired = true;
                scout.restarts = 0;
                self.launch(scout)
            }
            "stop" => {
                scout.desired = false;
                self.terminate(scout);
                Ok(())
            }
            "restart" => {
                self.terminate(scout);
                scout.desired = true;
                scout.restarts = 0;
                self.launch(scout)
            }
            _ => Err("Unsupported Scout command.".into()),
        }
    }
    fn control_all(&self, command: &str) -> Result<(), String> {
        match command {
            "start" => {
                let mut errors = Vec::new();
                for m in ["review", "keyword"] {
                    if let Err(error) = self.control(m, "start") {
                        errors.push(error);
                    }
                }
                if errors.is_empty() {
                    Ok(())
                } else {
                    Err(errors.join(" | "))
                }
            }
            "stop" => {
                for m in ["review", "keyword"] {
                    self.control(m, "stop")?;
                }
                Ok(())
            }
            _ => Err("Unsupported automation command.".into()),
        }
    }
    fn tick(&self) {
        if !self.owns_manager_lock() || self.updating.load(Ordering::SeqCst) {
            return;
        }
        let Ok(mut all) = self.lock_modes() else {
            return;
        };
        for scout in all.iter_mut() {
            if let Some(child) = scout.child.as_mut() {
                match child.try_wait() {
                    Ok(Some(status)) => {
                        scout.child.take();
                        #[cfg(windows)]
                        {
                            scout.job.take();
                        }
                        let stable = scout
                            .started_at
                            .map(|t| t.elapsed() >= Duration::from_secs(300))
                            .unwrap_or(false);
                        if stable {
                            scout.restarts = 0;
                        }
                        scout.started_at = None;
                        if !scout.desired {
                            scout.state = "Stopped".into();
                            continue;
                        }
                        let tail = file_tail(&log_file(&self.log_root, scout.name), 30);
                        if status.code() == Some(70)
                            || tail.contains("credentials were rejected")
                            || tail.contains("401 Unauthorized")
                        {
                            scout.desired = false;
                            scout.state = "Pairing required".into();
                            scout.last_error = Some("Check Scout pairing credentials.".into());
                            continue;
                        }
                        scout.restarts += 1;
                        if scout.restarts > 4 {
                            scout.desired = false;
                            scout.state = "Paused after repeated exits".into();
                            scout.last_error = Some(
                                "More than four consecutive failures. Manual action required."
                                    .into(),
                            );
                        } else {
                            scout.state = "Restart pending".into();
                            scout.next_try = Instant::now()
                                + Duration::from_secs((5_u64 << (scout.restarts - 1)).min(60));
                        }
                    }
                    Ok(None) => {}
                    Err(_) => {
                        scout.desired = false;
                        scout.state = "Process error".into();
                        scout.last_error = Some("Could not check process health.".into());
                    }
                }
            } else if scout.desired && Instant::now() >= scout.next_try {
                if let Err(error) = self.launch(scout) {
                    scout.restarts += 1;
                    scout.state = "Start failed".into();
                    scout.last_error = Some(error);
                    scout.next_try = Instant::now() + Duration::from_secs(30);
                    if scout.restarts > 4 {
                        scout.desired = false;
                        scout.state = "Paused after repeated exits".into();
                    }
                }
            }
        }
    }
    fn dashboard(&self) -> Dashboard {
        let version = fs::read_to_string(self.repo.join("apps/rrugc_scout/scout.py"))
            .unwrap_or_default()
            .lines()
            .find_map(|l| {
                l.trim()
                    .strip_prefix("CLIENT_VERSION = ")
                    .map(|s| s.trim_matches('"').to_string())
            })
            .unwrap_or_else(|| "unknown".into());
        let commit = execute_git(&self.repo, &["rev-parse", "--short=8", "HEAD"])
            .unwrap_or_else(|_| "unknown".into());
        let modes = self
            .lock_modes()
            .map(|a| a.iter().map(Scout::info).collect())
            .unwrap_or_default();
        Dashboard {
            version,
            commit,
            update_state: self
                .update_state
                .lock()
                .map(|s| s.clone())
                .unwrap_or_default(),
            paired: is_paired(&self.repo),
            updating: self.updating.load(Ordering::SeqCst),
            controller_available: self.owns_manager_lock(),
            modes,
        }
    }
    fn check_update(&self) -> Result<(), String> {
        self.require_manager_lock()?;
        if self.updating.swap(true, Ordering::SeqCst) {
            return Err("An update is already running.".into());
        }
        let result = self.apply_update();
        if let Ok(mut status) = self.update_state.lock() {
            *status = match &result {
                Ok(message) => message.clone(),
                Err(e) => e.clone(),
            };
        }
        self.updating.store(false, Ordering::SeqCst);
        result.map(|_| ())
    }
    fn apply_update(&self) -> Result<String, String> {
        // Git -C walks parent directories. Do not let a test folder or a
        // nested checkout accidentally update its parent Git repository.
        let worktree_root =
            PathBuf::from(execute_git(&self.repo, &["rev-parse", "--show-toplevel"])?);
        if worktree_root.canonicalize().ok() != self.repo.canonicalize().ok() {
            return Err(
                "Auto-update paused: Scout source folder is not the Git worktree root.".into(),
            );
        }
        if execute_git(&self.repo, &["branch", "--show-current"])? != "main" {
            return Err("Auto-update paused: checkout must be on main.".into());
        }
        if !execute_git(
            &self.repo,
            &["status", "--porcelain", "--untracked-files=no"],
        )?
        .is_empty()
        {
            return Err(
                "Auto-update paused: tracked local modifications. No files were overwritten."
                    .into(),
            );
        }
        execute_git(
            &self.repo,
            &[
                "fetch",
                "--quiet",
                "origin",
                "+refs/heads/main:refs/remotes/origin/main",
            ],
        )?;
        let local = execute_git(&self.repo, &["rev-parse", "HEAD"])?;
        let remote = execute_git(&self.repo, &["rev-parse", "origin/main"])?;
        if local == remote {
            return Ok("Up to date".into());
        }
        execute_git(&self.repo, &["merge-base", "--is-ancestor", "HEAD", "origin/main"])
            .map_err(|_| "Auto-update paused: local branch diverged from origin/main; running Scouts were not interrupted.".to_string())?;
        let diff = execute_git(&self.repo, &["diff", "--name-only", "HEAD..origin/main"])?;
        let restart = diff.lines().any(|p| {
            p.starts_with("apps/rrugc_scout/")
                || p.starts_with("scripts/start_scout")
                || p.starts_with("scout.local.env.example")
        });
        let mut previous = Vec::new();
        if restart {
            let mut modes = self.lock_modes()?;
            for scout in modes.iter_mut() {
                if scout.desired {
                    previous.push(scout.name);
                    self.terminate(scout);
                }
            }
        }
        let merged = execute_git(&self.repo, &["merge", "--ff-only", "origin/main"]);
        if restart {
            let mut modes = self.lock_modes()?;
            for scout in modes.iter_mut().filter(|m| previous.contains(&m.name)) {
                scout.desired = true;
                scout.restarts = 0;
                if let Err(error) = self.launch(scout) {
                    scout.state = "Start failed".into();
                    scout.last_error = Some(error);
                }
            }
        }
        merged?;
        if diff
            .lines()
            .any(|p| p.starts_with("apps/scout-manager-desktop/"))
        {
            Ok("Scout source updated; desktop installer update available".into())
        } else {
            Ok("Scout source updated".into())
        }
    }
    fn shutdown(&self) {
        if let Ok(mut modes) = self.lock_modes() {
            for scout in modes.iter_mut() {
                scout.desired = false;
                self.terminate(scout);
            }
        }
    }
}
#[cfg(windows)]
mod winjob {
    use std::mem::{size_of, zeroed};
    use std::os::windows::io::AsRawHandle;
    use std::process::Child;
    use windows_sys::Win32::Foundation::{CloseHandle, HANDLE, WAIT_ABANDONED, WAIT_OBJECT_0};
    use windows_sys::Win32::System::Threading::{CreateMutexW, ReleaseMutex, WaitForSingleObject};

    // This matches the lock held by scripts/start_scout_manager.ps1.
    // A second Manager becomes read-only rather than launching duplicate Scouts.
    pub struct ManagerLock(HANDLE);
    unsafe impl Send for ManagerLock {}
    unsafe impl Sync for ManagerLock {}
    impl ManagerLock {
        pub fn acquire() -> Result<Option<Self>, String> {
            let wide: Vec<u16> = "Local\\CreativeAssetManager.RrugcScout.Manager"
                .encode_utf16()
                .chain(std::iter::once(0))
                .collect();
            unsafe {
                let handle = CreateMutexW(std::ptr::null(), 0, wide.as_ptr());
                if handle.is_null() {
                    return Err("Could not initialize Scout Manager single-instance lock.".into());
                }
                match WaitForSingleObject(handle, 0) {
                    WAIT_OBJECT_0 | WAIT_ABANDONED => Ok(Some(Self(handle))),
                    _ => {
                        CloseHandle(handle);
                        Ok(None)
                    }
                }
            }
        }
    }
    impl Drop for ManagerLock {
        fn drop(&mut self) {
            unsafe {
                ReleaseMutex(self.0);
                CloseHandle(self.0);
            }
        }
    }
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
        SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };
    pub struct Job(HANDLE);
    unsafe impl Send for Job {}
    impl Job {
        pub fn attach(child: &Child) -> Result<Self, String> {
            unsafe {
                let handle = CreateJobObjectW(std::ptr::null(), std::ptr::null());
                if handle.is_null() {
                    return Err("Unable to create process isolation job.".into());
                }
                let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = zeroed();
                info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
                let configured = SetInformationJobObject(
                    handle,
                    JobObjectExtendedLimitInformation,
                    &info as *const _ as _,
                    size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
                );
                let attached = configured != 0
                    && AssignProcessToJobObject(handle, child.as_raw_handle() as HANDLE) != 0;
                if !attached {
                    CloseHandle(handle);
                    return Err("Unable to isolate Scout child process.".into());
                }
                Ok(Self(handle))
            }
        }
    }
    impl Drop for Job {
        fn drop(&mut self) {
            unsafe {
                CloseHandle(self.0);
            }
        }
    }
}
#[tauri::command]
fn dashboard(state: tauri::State<'_, Arc<Controller>>) -> Dashboard {
    state.dashboard()
}
#[tauri::command]
fn log_tail(
    state: tauri::State<'_, Arc<Controller>>,
    mode: String,
    max_lines: usize,
) -> Result<String, String> {
    if mode != "review" && mode != "keyword" {
        return Err("Unknown log source.".into());
    }
    Ok(file_tail(
        &log_file(&state.log_root, &mode),
        max_lines.min(120),
    ))
}
#[tauri::command]
fn control_scout(
    state: tauri::State<'_, Arc<Controller>>,
    mode: String,
    command: String,
) -> Result<(), String> {
    state.control(&mode, &command)
}
#[tauri::command]
fn control_all(state: tauri::State<'_, Arc<Controller>>, command: String) -> Result<(), String> {
    state.control_all(&command)
}
#[tauri::command]
fn check_update(state: tauri::State<'_, Arc<Controller>>) -> Result<(), String> {
    state.check_update()
}
#[tauri::command]
fn save_pairing(
    state: tauri::State<'_, Arc<Controller>>,
    agent_id: String,
    token: String,
) -> Result<(), String> {
    state.require_manager_lock()?;
    if agent_id.is_empty()
        || agent_id.len() > 128
        || !agent_id
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '-')
    {
        return Err("Enter a valid Scout Agent ID.".into());
    }
    if token.len() > 1024 || token.chars().any(|c| c == '\n' || c == '\r' || c == '\0') {
        return Err("Invalid Scout token.".into());
    }
    let current = config_values(&state.repo);
    let previous_id = current
        .iter()
        .find(|(k, _)| k == "RRUGC_AGENT_ID")
        .map(|(_, v)| v.as_str())
        .unwrap_or("");
    let had_token = current
        .iter()
        .any(|(k, v)| k == "RRUGC_SCOUT_TOKEN" && !v.is_empty());
    if token.is_empty() && (!had_token || previous_id != agent_id) {
        return Err("A new token is required for a new Agent ID.".into());
    }
    // Do not expose the token in the frontend read API, status, log or error.
    let mut lines = fs::read_to_string(state.repo.join("scout.local.env"))
        .unwrap_or_default()
        .lines()
        .filter(|l| {
            !l.trim().starts_with("RRUGC_AGENT_ID=") && !l.trim().starts_with("RRUGC_SCOUT_TOKEN=")
        })
        .map(str::to_string)
        .collect::<Vec<_>>();
    lines.push(format!("RRUGC_AGENT_ID={}", agent_id));
    lines.push(format!(
        "RRUGC_SCOUT_TOKEN={}",
        if token.is_empty() {
            current
                .iter()
                .find(|(k, _)| k == "RRUGC_SCOUT_TOKEN")
                .map(|(_, v)| v.as_str())
                .unwrap_or("")
        } else {
            token.as_str()
        }
    ));
    let file = state.repo.join("scout.local.env");
    let tmp = file.with_extension("env.pending");
    fs::write(&tmp, lines.join("\n") + "\n")
        .map_err(|_| "Unable to save local pairing.".to_string())?;
    // Never delete the existing pairing file if an atomic replacement fails.
    if fs::rename(&tmp, &file).is_err() {
        let _ = fs::remove_file(&tmp);
        return Err(
            "Unable to replace local pairing safely; original file was preserved.".to_string(),
        );
    }
    Ok(())
}
pub fn run() {
    let controller = Controller::new().expect("A compatible scan_pinterest checkout is required");
    let state = Arc::new(controller);
    let polling = Arc::clone(&state);
    thread::spawn(move || {
        thread::sleep(Duration::from_secs(2));
        loop {
            polling.tick();
            thread::sleep(Duration::from_secs(2));
        }
    });
    if std::env::var_os("CAM_SCOUT_DISABLE_UPDATES").is_none() {
        let updating = Arc::clone(&state);
        thread::spawn(move || loop {
            thread::sleep(Duration::from_secs(60));
            let _ = updating.check_update();
        });
    }
    tauri::Builder::default()
        .manage(state)
        .setup(|app| {
            let open = MenuItem::with_id(app, "open", "Open Scout Manager", true, None::<&str>)?;
            let pause = MenuItem::with_id(app, "pause", "Pause both Scouts", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit and stop Scouts", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&open, &pause, &quit])?;
            let icon = app.default_window_icon().cloned();
            let mut tray = TrayIconBuilder::new()
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "open" => {
                        if let Some(w) = app.get_webview_window("main") {
                            let _ = w.show();
                            let _ = w.set_focus();
                        }
                    }
                    "pause" => {
                        let _ = app.state::<Arc<Controller>>().control_all("stop");
                    }
                    "quit" => {
                        app.state::<Arc<Controller>>().shutdown();
                        app.exit(0);
                    }
                    _ => {}
                })
                .on_tray_icon_event(|tray, _| {
                    if let Some(w) = tray.app_handle().get_webview_window("main") {
                        let _ = w.show();
                        let _ = w.set_focus();
                    }
                });
            if let Some(icon) = icon {
                tray = tray.icon(icon);
            }
            let _ = tray.build(app)?;
            let managed = app.state::<Arc<Controller>>();
            // Preserve legacy behavior: both Scouts auto-start after valid pairing.
            if managed.owns_manager_lock() && is_paired(&managed.repo) {
                let _ = managed.control_all("start");
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            match event {
                WindowEvent::CloseRequested { api, .. } => {
                    api.prevent_close();
                    let _ = window.hide(); // child processes continue in tray
                }
                WindowEvent::Resized(_) => {
                    if window.is_minimized().unwrap_or(false) {
                        let _ = window.hide();
                    }
                }
                _ => {}
            }
        })
        .invoke_handler(tauri::generate_handler![
            dashboard,
            log_tail,
            control_scout,
            control_all,
            check_update,
            save_pairing
        ])
        .run(tauri::generate_context!())
        .expect("Scout Manager desktop runtime failed");
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn pairing_needs_both_fields() {
        let scratch = std::env::temp_dir().join(format!("cam-scout-test-{}", std::process::id()));
        fs::create_dir_all(&scratch).unwrap();
        fs::write(
            scratch.join("scout.local.env"),
            "RRUGC_AGENT_ID=123\nRRUGC_SCOUT_TOKEN=\n",
        )
        .unwrap();
        assert!(!is_paired(&scratch));
        fs::write(
            scratch.join("scout.local.env"),
            "RRUGC_AGENT_ID=123\nRRUGC_SCOUT_TOKEN=test-token\n",
        )
        .unwrap();
        assert!(is_paired(&scratch));
        let _ = fs::remove_dir_all(scratch);
    }
    #[test]
    fn no_external_path_in_log_name() {
        let root = Path::new("C:/logs");
        assert_eq!(log_file(root, "review"), root.join("review.stdout.log"));
    }

    #[cfg(windows)]
    #[test]
    fn simulated_scout_process_starts_and_stops_without_real_credentials() {
        let scratch = std::env::temp_dir().join(format!("cam-scout-native-{}", std::process::id()));
        let _ = fs::remove_dir_all(&scratch);
        let scripts = scratch.join("scripts");
        let logs = scratch.join("logs");
        fs::create_dir_all(&scripts).unwrap();
        fs::create_dir_all(&logs).unwrap();
        fs::write(
            scratch.join("scout.local.env"),
            "RRUGC_AGENT_ID=simulated-id\nRRUGC_SCOUT_TOKEN=fixture-not-real\n",
        )
        .unwrap();
        fs::write(scripts.join("start_scout_auto_update.ps1"),
            "param([switch]$SkipUpdate, [switch]$KeywordMode)\nWrite-Host 'FAKE_SCOUT_STARTED'\nStart-Sleep -Seconds 30\n").unwrap();
        let controller = Controller {
            // Real locate_repo canonicalizes first; keep the Windows smoke
            // fixture on that exact path conversion route.
            repo: shell_friendly_path(scratch.canonicalize().unwrap()),
            log_root: logs.clone(),
            modes: Mutex::new(vec![Scout::new("review"), Scout::new("keyword")]),
            manager_lock: None,
            update_state: Mutex::new("fixture".into()),
            updating: AtomicBool::new(false),
        };
        assert!(!controller.repo.to_string_lossy().starts_with(r"\\?\"));
        let mut mock = Scout::new("review");
        controller
            .launch(&mut mock)
            .expect("fixture Scout must launch into a Windows Job");
        assert!(mock.child.as_mut().unwrap().try_wait().unwrap().is_none());
        std::thread::sleep(Duration::from_millis(700));
        assert!(file_tail(&log_file(&logs, "review"), 10).contains("FAKE_SCOUT_STARTED"));
        controller.terminate(&mut mock);
        assert!(mock.child.is_none());
        assert_eq!(mock.state, "Stopped");
        fs::remove_dir_all(scratch).unwrap();
    }
}