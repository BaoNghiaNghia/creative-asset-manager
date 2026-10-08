#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
use serde::{Deserialize, Serialize};
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
    automation_enabled: bool,
    modes: Vec<ModeInfo>,
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
struct KeywordSummary {
    total_keywords: u64,
    added_24h: u64,
    added_7d: u64,
    analysis_pending: u64,
    analysis_oldest_wait_seconds: u64,
    analysis_backpressure_active: bool,
    keyword_fair_share_limited: bool,
    keyword_next_slot_seconds: u64,
    gemini_backup_keys_configured: u64,
    last_created_at: Option<String>,
    last_updated_at: Option<String>,
    fetched_at: String,
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
    automation_enabled: AtomicBool,
    cached_commit: Mutex<String>,
    shutdown_requested: AtomicBool,
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
    let mut process = Command::new("git");
    process.arg("-C").arg(repo).args(args);
    hide_console(&mut process);
    let output = process.output()
        .map_err(|_| "Git is not installed or unavailable.".to_string())?;
    if !output.status.success() {
        return Err("Git operation failed. Check your repository and network.".into());
    }
    Ok(String::from_utf8_lossy(&output.stdout).trim().to_string())
}
fn read_local_commit(repo: &Path) -> String {
    // UI startup must not launch Git even when its executable is a console shim.
    let git_path = repo.join(".git");
    let git_dir = if git_path.is_dir() {
        git_path
    } else {
        let pointer = fs::read_to_string(&git_path).unwrap_or_default();
        let Some(value) = pointer.trim().strip_prefix("gitdir: ") else {
            return "unknown".into();
        };
        let path = PathBuf::from(value);
        if path.is_absolute() { path } else { repo.join(path) }
    };
    let head = fs::read_to_string(git_dir.join("HEAD")).unwrap_or_default();
    let head = head.trim();
    let hash = if let Some(reference) = head.strip_prefix("ref: ") {
        fs::read_to_string(git_dir.join(reference)).unwrap_or_default()
    } else {
        head.to_string()
    };
    let hash = hash.trim();
    if hash.len() >= 8 && hash.chars().all(|c| c.is_ascii_hexdigit()) {
        hash[..8].to_string()
    } else {
        "unknown".into()
    }
}

fn commit_short(repo: &Path) -> String {
    execute_git(repo, &["rev-parse", "--short=8", "HEAD"])
        .unwrap_or_else(|_| "unknown".into())
}

fn hide_console(process: &mut Command) {
    // Git (including a Git launcher that shells out to cmd.exe) must not
    // create a visible console during each updater check.
    #[cfg(windows)]
    { process.creation_flags(0x08000000); } // CREATE_NO_WINDOW
    #[cfg(not(windows))]
    { let _ = process; }
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
        let commit = read_local_commit(&repo);
        Ok(Self {
            repo,
            log_root,
            modes: Mutex::new(vec![Scout::new("review"), Scout::new("keyword")]),
            #[cfg(windows)]
            manager_lock: winjob::ManagerLock::acquire()?,
            update_state: Mutex::new("Idle - press Run automation".into()),
            updating: AtomicBool::new(false),
            automation_enabled: AtomicBool::new(false),
            cached_commit: Mutex::new(commit),
            shutdown_requested: AtomicBool::new(false),
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
        hide_console(&mut process); // no flashing PowerShell console
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
            // Avoid blocking UI / shutdown indefinitely if PowerShell, Chrome,
            // or an antivirus holds an exit handle. Job Object already killed
            // the complete child tree on Windows.
            let deadline = Instant::now() + Duration::from_secs(2);
            while Instant::now() < deadline {
                if child.try_wait().ok().flatten().is_some() { break; }
                thread::sleep(Duration::from_millis(30));
            }
        }
        scout.state = "Stopped".into();
        scout.started_at = None;
    }
    fn control(&self, mode: &str, command: &str) -> Result<(), String> {
        self.require_manager_lock()?;
        if self.updating.load(Ordering::SeqCst) {
            return Err("Source update is in progress.".into());
        }
        if (command == "start" || command == "restart") && !self.automation_enabled.load(Ordering::SeqCst) {
            return Err("Click Run automation before starting an individual Scout.".into());
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
                self.require_manager_lock()?;
                self.automation_enabled.store(true, Ordering::SeqCst);
                if let Ok(mut status) = self.update_state.lock() { *status = "Auto update active".into(); }
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
                self.require_manager_lock()?;
                self.automation_enabled.store(false, Ordering::SeqCst);
                for m in ["review", "keyword"] {
                    self.control(m, "stop")?;
                }
                if let Ok(mut status) = self.update_state.lock() { *status = "Paused - press Run automation".into(); }
                Ok(())
            }
            _ => Err("Unsupported automation command.".into()),
        }
    }
    fn tick(&self) {
        if !self.owns_manager_lock() || self.shutdown_requested.load(Ordering::SeqCst) || self.updating.load(Ordering::SeqCst) {
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
        // No Git process on the 2-second UI polling path.
        let commit = self.cached_commit.lock().map(|v| v.clone()).unwrap_or_else(|_| "unknown".into());
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
            automation_enabled: self.automation_enabled.load(Ordering::SeqCst),
            modes,
        }
    }
    fn check_update(&self) -> Result<(), String> {
        self.require_manager_lock()?;
        if self.shutdown_requested.load(Ordering::SeqCst) {
            return Err("Scout Manager is shutting down.".into());
        }
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
        if merged.is_ok() {
            if let Ok(mut cached) = self.cached_commit.lock() {
                *cached = commit_short(&self.repo);
            }
        }
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
        self.shutdown_requested.store(true, Ordering::SeqCst);
        self.automation_enabled.store(false, Ordering::SeqCst);
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
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        FindWindowW, SetForegroundWindow, ShowWindow, SW_RESTORE,
    };
    pub fn focus_existing_manager() -> bool {
        let title: Vec<u16> = "RRUGC Scout Manager".encode_utf16().chain(std::iter::once(0)).collect();
        unsafe {
            let window = FindWindowW(std::ptr::null(), title.as_ptr());
            if window.is_null() { return false; }
            ShowWindow(window, SW_RESTORE);
            SetForegroundWindow(window);
            true
        }
    }

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
async fn keyword_summary(
    state: tauri::State<'_, Arc<Controller>>,
) -> Result<KeywordSummary, String> {
    let values = config_values(&state.repo);
    let get = |key: &str| values.iter().find(|(k, _)| k == key).map(|(_, v)| v.as_str());
    let agent_id = get("RRUGC_AGENT_ID").filter(|v| !v.is_empty())
        .ok_or("Pairing required to read live keyword counts.")?;
    let token = get("RRUGC_SCOUT_TOKEN").filter(|v| !v.is_empty())
        .ok_or("Pairing required to read live keyword counts.")?;
    if !agent_id.chars().all(|c| c.is_ascii_alphanumeric() || c == '-') {
        return Err("Invalid paired Agent ID.".into());
    }
    let base = get("RRUGC_BASE_URL").unwrap_or("https://creative-assets.ddns.net");
    // Require HTTPS so the agent token is never sent over plaintext.
    let parsed = reqwest::Url::parse(base).map_err(|_| "Invalid Scout API URL.")?;
    if parsed.scheme() != "https" || parsed.host_str().is_none() || !parsed.username().is_empty()
        || parsed.password().is_some() || parsed.query().is_some() || parsed.fragment().is_some() {
        return Err("Scout API must be an HTTPS origin.".into());
    }
    let url = format!(
        "{}/api/v1/realistic-review-ugc/scout-agents/{}/keyword-analysis/summary",
        base.trim_end_matches('/'), agent_id
    );
    let response = reqwest::Client::new()
        .get(&url)
        .bearer_auth(token)
        .timeout(Duration::from_secs(8))
        .send()
        .await
        .map_err(|_| "Keyword API unreachable. Check network and Scout API.".to_string())?;
    if !response.status().is_success() {
        return Err(format!("Keyword API returned HTTP {}.", response.status().as_u16()));
    }
    response.json::<KeywordSummary>().await
        .map_err(|_| "Keyword API returned invalid summary data.".into())
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
async fn control_scout(
    state: tauri::State<'_, Arc<Controller>>,
    mode: String,
    command: String,
) -> Result<(), String> {
    let controller = state.inner().clone();
    // Process launch, recovery and shutdown are blocking OS operations.
    // Never perform them on the WebView/Windows event thread.
    tauri::async_runtime::spawn_blocking(move || controller.control(&mode, &command))
        .await
        .map_err(|_| "Scout process operation failed unexpectedly.".to_string())?
}
#[tauri::command]
async fn control_all(state: tauri::State<'_, Arc<Controller>>, command: String) -> Result<(), String> {
    let controller = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || controller.control_all(&command))
        .await
        .map_err(|_| "Scout automation operation failed unexpectedly.".to_string())?
}
#[tauri::command]
async fn check_update(state: tauri::State<'_, Arc<Controller>>) -> Result<(), String> {
    let controller = state.inner().clone();
    // Git fetch can take minutes on a slow connection. Keep the UI responsive.
    tauri::async_runtime::spawn_blocking(move || controller.check_update())
        .await
        .map_err(|_| "Scout source update failed unexpectedly.".to_string())?
}
// Always exit off the Windows message-pump thread; stopping two managed
// process trees must never freeze the close or tray menu handlers.
fn request_shutdown(app: tauri::AppHandle) {
    let controller = app.state::<Arc<Controller>>().inner().clone();
    if controller.shutdown_requested.swap(true, Ordering::SeqCst) { return; }
    // Keep the UI responsive and guarantee a bounded quit even when a child
    // or an updater stalls. Windows Job Object handles close on process exit.
    let timeout_app = app.clone();
    thread::spawn(move || {
        thread::sleep(Duration::from_secs(10));
        timeout_app.exit(0);
    });
    thread::spawn(move || {
        controller.shutdown();
        app.exit(0);
    });
}
#[tauri::command]
fn quit_manager(app: tauri::AppHandle) {
    request_shutdown(app);
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
    #[cfg(windows)]
    if !controller.owns_manager_lock() && winjob::focus_existing_manager() {
        // Do not create a second WebView/tray icon. Reopen the original Manager.
        return;
    }
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
            // Zero background Git processes while waiting for Run automation.
            if updating.automation_enabled.load(Ordering::SeqCst)
                && !updating.shutdown_requested.load(Ordering::SeqCst) {
                let _ = updating.check_update();
            }
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
                        let manager = app.state::<Arc<Controller>>().inner().clone();
                        thread::spawn(move || { let _ = manager.control_all("stop"); });
                    }
                    "quit" => request_shutdown(app.clone()),
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    // Do not refocus the window on every mouse movement or
                    // icon event: that causes drag loss and apparent freezes.
                    if matches!(event, tauri::tray::TrayIconEvent::DoubleClick { .. }) {
                        if let Some(w) = tray.app_handle().get_webview_window("main") {
                            let _ = w.show();
                            let _ = w.set_focus();
                        }
                    }
                });
            if let Some(icon) = icon {
                tray = tray.icon(icon);
            }
            let _ = tray.build(app)?;
            let managed = app.state::<Arc<Controller>>();
            // Manual-start policy: never start either Scout on app startup.
            // Only the Run automation button enables processing and updates.
            let _ = managed;
            Ok(())
        })
        .on_window_event(|window, event| {
            match event {
                WindowEvent::CloseRequested { api, .. } => {
                    api.prevent_close();
                    let _ = window.hide(); // child processes continue in tray
                }
                _ => {}
            }
        })
        .invoke_handler(tauri::generate_handler![
            dashboard,
            keyword_summary,
            log_tail,
            control_scout,
            control_all,
            check_update,
            save_pairing,
            quit_manager
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
    fn commit_read_from_git_metadata_without_spawning_git() {
        let root = std::env::temp_dir().join(format!("cam-git-meta-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(root.join(".git/refs/heads")).unwrap();
        fs::write(root.join(".git/HEAD"), "ref: refs/heads/main\n").unwrap();
        fs::write(root.join(".git/refs/heads/main"), "1234567890abcdef1234567890abcdef12345678\n").unwrap();
        assert_eq!(read_local_commit(&root), "12345678");
        fs::remove_dir_all(&root).unwrap();
    }
    #[test]
    fn scout_is_idle_until_explicitly_requested() {
        let review = Scout::new("review");
        let keyword = Scout::new("keyword");
        assert!(!review.desired);
        assert!(!keyword.desired);
        assert!(review.child.is_none() && keyword.child.is_none());
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
            automation_enabled: AtomicBool::new(false),
            cached_commit: Mutex::new("fixture".into()),
            shutdown_requested: AtomicBool::new(false),
        };
        assert!(!controller.repo.to_string_lossy().starts_with(r"\\?\"));
        let mut mock = Scout::new("review");
        controller
            .launch(&mut mock)
            .expect("fixture Scout must launch into a Windows Job");
        assert!(mock.child.as_mut().unwrap().try_wait().unwrap().is_none());
        // Cold GitHub Windows runners may take several seconds to bootstrap
        // PowerShell. Poll the real output instead of assuming a 700ms start.
        let deadline = Instant::now() + Duration::from_secs(12);
        let mut started = false;
        while Instant::now() < deadline {
            if file_tail(&log_file(&logs, "review"), 10).contains("FAKE_SCOUT_STARTED") {
                started = true;
                break;
            }
            if mock.child.as_mut().unwrap().try_wait().unwrap().is_some() {
                break;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        let stderr = file_tail(&logs.join("review.stderr.log"), 30);
        controller.terminate(&mut mock);
        assert!(started, "Windows smoke Scout did not boot: {stderr}");
        assert!(mock.child.is_none());
        assert_eq!(mock.state, "Stopped");
        fs::remove_dir_all(scratch).unwrap();
    }
}