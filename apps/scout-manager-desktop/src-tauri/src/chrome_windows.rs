//! Windows-only visibility changes for Chrome windows owned by this Manager.
//! Scout Job Objects provide exact process ownership; never search every Chrome
//! instance by name and never modify browser profiles or stop a process.

#[cfg(windows)]
mod windows {
    use std::collections::HashSet;
    use windows_sys::Win32::Foundation::{BOOL, HWND, LPARAM};
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        EnumWindows, GetClassNameW, GetWindowThreadProcessId, IsIconic, IsWindowVisible,
        ShowWindow, SW_MINIMIZE, SW_RESTORE,
    };

    #[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
    struct WindowKey {
        handle: isize,
        pid: u32,
    }

    #[derive(Default)]
    pub struct WindowTracker {
        // Only windows actually minimized by this feature may be restored.
        changed: HashSet<WindowKey>,
    }

    impl WindowTracker {
        pub fn new() -> Self { Self::default() }

        pub fn sync(&mut self, minimize: bool, owned_pids: &HashSet<u32>) {
            let windows = enumerate_chrome_windows();
            let live: HashSet<WindowKey> = windows.iter().copied().collect();
            // HWND reuse cannot accidentally restore somebody else's browser.
            self.changed.retain(|key| live.contains(key));

            if !minimize {
                for key in self.changed.drain() {
                    unsafe {
                        let window = key.handle as HWND;
                        // Enumerating the HWND+PID above also guards against reuse.
                        if IsIconic(window) != 0 { ShowWindow(window, SW_RESTORE); }
                    }
                }
                return;
            }

            for key in windows {
                if !owned_pids.contains(&key.pid) || self.changed.contains(&key) {
                    continue;
                }
                unsafe {
                    let window = key.handle as HWND;
                    // Respect browsers that were already minimized manually.
                    if IsWindowVisible(window) != 0 && IsIconic(window) == 0 {
                        ShowWindow(window, SW_MINIMIZE);
                        if IsIconic(window) != 0 { self.changed.insert(key); }
                    }
                }
            }
        }
    }

    unsafe extern "system" fn collect_chrome(window: HWND, data: LPARAM) -> BOOL {
        if IsWindowVisible(window) == 0 { return 1; }
        let mut class_name = [0u16; 64];
        let len = GetClassNameW(window, class_name.as_mut_ptr(), class_name.len() as i32);
        if len <= 0 { return 1; }
        let name = &class_name[..len as usize];
        let chrome_prefix: Vec<u16> = "Chrome_WidgetWin_".encode_utf16().collect();
        if !name.starts_with(&chrome_prefix) { return 1; }

        let mut pid = 0u32;
        GetWindowThreadProcessId(window, &mut pid);
        if pid != 0 {
            let found = &mut *(data as *mut Vec<WindowKey>);
            found.push(WindowKey { handle: window as isize, pid });
        }
        1
    }

    fn enumerate_chrome_windows() -> Vec<WindowKey> {
        let mut found = Vec::new();
        unsafe {
            EnumWindows(Some(collect_chrome), &mut found as *mut Vec<WindowKey> as LPARAM);
        }
        found
    }
}
#[cfg(windows)]
pub use windows::WindowTracker;

#[cfg(not(windows))]
pub struct WindowTracker;
#[cfg(not(windows))]
impl WindowTracker {
    pub fn new() -> Self { Self }
    pub fn sync(&mut self, _minimize: bool, _owned_pids: &std::collections::HashSet<u32>) {}
}
