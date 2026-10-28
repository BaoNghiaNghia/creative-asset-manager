//! Native startup tests use isolated fake launchers and their own Manager mutex.
use super::*;
use startup::StartupAction::{Idle, StartAutomation};
use std::os::windows::io::{AsHandle, AsRawHandle, OwnedHandle};
use windows_sys::Win32::Foundation::WAIT_OBJECT_0;
use windows_sys::Win32::System::Threading::WaitForSingleObject;

struct Fixture {
    controller: Controller,
    root: PathBuf,
}

impl Fixture {
    fn new(label: &str, paired: bool, owner: bool) -> Self {
        let root = std::env::temp_dir().join(format!(
            "cam-scout-startup-{label}-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(root.join("scripts")).unwrap();
        fs::create_dir_all(root.join("logs")).unwrap();
        if paired {
            fs::write(
                root.join("scout.local.env"),
                // Invalid origin makes normal best-effort lease release return
                // before constructing a client. These tests never call CAM.
                "RRUGC_AGENT_ID=startup-fixture\nRRUGC_SCOUT_TOKEN=fixture-not-real\nRRUGC_BASE_URL=invalid-fixture-origin\n",
            ).unwrap();
        }
        fs::write(
            root.join("scripts/start_scout_auto_update.ps1"),
            "param([switch]$SkipUpdate, [switch]$KeywordMode, [switch]$CapStackMode)\n\
             $mode = 'review'\n\
             if ($KeywordMode) { $mode = 'keyword' }\n\
             if ($CapStackMode) { $mode = 'cap_stack' }\n\
             Write-Host ('FAKE_SCOUT_STARTED:' + $mode)\n\
             Start-Sleep -Seconds 120\n",
        ).unwrap();
        let manager_lock = if owner {
            winjob::ManagerLock::acquire_named(&format!(
                "Local\\CreativeAssetManager.RrugcScout.StartupTest.{label}.{}",
                std::process::id()
            )).unwrap()
        } else {
            None
        };
        assert_eq!(manager_lock.is_some(), owner);
        let controller = Controller {
            repo: shell_friendly_path(root.canonicalize().unwrap()),
            log_root: root.join("logs"),
            modes: Mutex::new(vec![
                Scout::new("review"),
                Scout::new("keyword"),
                Scout::new("cap_stack"),
            ]),
            branding_pilot: Mutex::new(branding_pilot::BrandPilot::new()),
            manager_lock,
            update_state: Mutex::new("Idle fixture".into()),
            updating: AtomicBool::new(false),
            automation_enabled: AtomicBool::new(false),
            chrome_minimized: AtomicBool::new(false),
            chrome_windows: Mutex::new(chrome_windows::WindowTracker::new()),
            cached_commit: Mutex::new("fixture".into()),
            shutdown_requested: AtomicBool::new(false),
        };
        Self { controller, root }
    }

    fn process_ids(&self) -> Vec<u32> {
        self.controller.lock_modes().unwrap().iter()
            .filter_map(|m| m.child.as_ref().map(Child::id)).collect()
    }

    fn process_handles(&self) -> Vec<OwnedHandle> {
        self.controller.lock_modes().unwrap().iter()
            .filter_map(|m| m.child.as_ref())
            .map(|child| child.as_handle().try_clone_to_owned().unwrap()).collect()
    }

    fn wait_ready(&self, modes: &[&str]) {
        let deadline = Instant::now() + Duration::from_secs(30);
        while Instant::now() < deadline {
            if modes.iter().all(|mode| {
                file_tail(&log_file(&self.controller.log_root, mode), 10)
                    .contains(&format!("FAKE_SCOUT_STARTED:{mode}"))
            }) {
                return;
            }
            thread::sleep(Duration::from_millis(100));
        }
        panic!("Isolated startup fixture did not become ready within 30 seconds");
    }

    fn assert_paused(&self) {
        assert!(!self.controller.automation_enabled.load(Ordering::SeqCst));
        for mode in self.controller.lock_modes().unwrap().iter() {
            assert!(!mode.desired);
            assert!(mode.child.is_none());
            assert!(mode.job.is_none());
        }
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        self.controller.shutdown();
        let _ = fs::remove_dir_all(&self.root);
    }
}

fn assert_processes_exited(handles: Vec<OwnedHandle>) {
    for handle in handles {
        // Cloned handles remain valid after Controller drops its Child,
        // proving the actual process exited, rather than just losing its PID.
        assert_eq!(
            unsafe { WaitForSingleObject(handle.as_raw_handle() as _, 5_000) },
            WAIT_OBJECT_0,
            "A Manager-owned fixture process survived Stop"
        );
    }
}

#[test]
fn default_startup_stays_idle_and_unpaired_opt_in_fails_closed() {
    let fixture = Fixture::new("idle", false, true);
    fixture.controller.apply_startup(Idle).unwrap();
    fixture.assert_paused();
    let error = fixture.controller.apply_startup(StartAutomation).unwrap_err();
    assert!(error.contains("Pairing required"));
    fixture.assert_paused();
}

#[test]
fn startup_without_manager_ownership_never_launches_scouts() {
    let fixture = Fixture::new("not-owner", true, false);
    let error = fixture.controller.apply_startup(StartAutomation).unwrap_err();
    assert!(error.contains("Another Scout Manager"));
    fixture.assert_paused();
}

#[test]
fn explicit_startup_starts_all_three_once_and_stop_reaps_owned_processes() {
    let fixture = Fixture::new("all-modes", true, true);
    fixture.controller.apply_startup(StartAutomation).unwrap();
    fixture.wait_ready(&["review", "keyword", "cap_stack"]);
    let ids = fixture.process_ids();
    assert_eq!(ids.len(), 3);
    let handles = fixture.process_handles();
    fixture.controller.apply_startup(StartAutomation).unwrap();
    assert_eq!(fixture.process_ids(), ids, "Repeated Start must not duplicate a mode");
    fixture.controller.control_all("stop").unwrap();
    fixture.assert_paused();
    assert_processes_exited(handles);
}

#[test]
fn startup_failure_in_third_mode_rolls_back_live_owned_scouts() {
    let fixture = Fixture::new("rollback", true, true);
    fixture.controller.automation_enabled.store(true, Ordering::SeqCst);
    fixture.controller.control("review", "start").unwrap();
    fixture.controller.control("keyword", "start").unwrap();
    fixture.wait_ready(&["review", "keyword"]);
    let handles = fixture.process_handles();
    assert_eq!(handles.len(), 2);
    // CapStack log creation fails after the other two modes are alive.
    fs::create_dir_all(log_file(&fixture.controller.log_root, "cap_stack")).unwrap();
    let error = fixture.controller.apply_startup(StartAutomation).unwrap_err();
    assert!(error.starts_with("All Scouts paused:"));
    fixture.assert_paused();
    assert_processes_exited(handles);
}

#[test]
fn startup_refuses_shutdown_and_update_states() {
    let fixture = Fixture::new("lifecycle", true, true);
    fixture.controller.shutdown_requested.store(true, Ordering::SeqCst);
    assert!(fixture.controller.apply_startup(StartAutomation).is_err());
    fixture.assert_paused();
    fixture.controller.shutdown_requested.store(false, Ordering::SeqCst);
    fixture.controller.updating.store(true, Ordering::SeqCst);
    assert!(fixture.controller.apply_startup(StartAutomation).is_err());
    fixture.assert_paused();
}
