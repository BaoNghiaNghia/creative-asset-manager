//! Bounded lifecycle policy for the paired CAM heartbeat, never browser work.
//! The owner must hold the same mutex across this policy and process start/stop.
use std::time::{Duration, Instant};

const MAX_START_ATTEMPTS: u8 = 4;
const STABLE_RUNNING: Duration = Duration::from_secs(60);

#[derive(Clone, Copy, Debug)]
pub(crate) struct StartGate {
    pub paired: bool,
    pub owns_manager_lock: bool,
    pub updating: bool,
    pub shutting_down: bool,
}

impl StartGate {
    fn allows_start(self) -> bool {
        self.paired && self.owns_manager_lock && !self.updating && !self.shutting_down
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum StartDecision {
    Wait,
    Start,
    Paused,
}

pub(crate) struct CamAgentSupervisor {
    desired: bool,
    attempts: u8,
    started_at: Option<Instant>,
}

impl CamAgentSupervisor {
    pub fn new() -> Self {
        Self { desired: true, attempts: 0, started_at: None }
    }

    pub fn request_start(&mut self) {
        self.desired = true;
        self.attempts = 0;
        self.started_at = None;
    }

    pub fn stop(&mut self) {
        self.desired = false;
        self.started_at = None;
    }

    pub fn next_start(
        &mut self, gate: StartGate, running: bool, now: Instant,
    ) -> StartDecision {
        if !self.desired || !gate.allows_start() {
            return StartDecision::Wait;
        }
        if running {
            // Spawning a process is not proof of health. Retain the attempt
            // budget until it has actually stayed alive for a full minute.
            if self.started_at.is_some_and(|at| {
                now.saturating_duration_since(at) >= STABLE_RUNNING
            }) {
                self.attempts = 0;
                self.started_at = None;
            }
            return StartDecision::Wait;
        }
        if self.attempts >= MAX_START_ATTEMPTS {
            self.desired = false;
            return StartDecision::Paused;
        }
        self.attempts += 1;
        self.started_at = None;
        StartDecision::Start
    }

    pub fn started(&mut self, now: Instant) {
        self.started_at = Some(now);
    }

    pub fn launch_failed(&mut self) -> bool {
        self.started_at = None;
        if self.attempts >= MAX_START_ATTEMPTS {
            self.desired = false;
            true
        } else {
            false
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{mpsc, Arc, Mutex};
    use std::thread;

    fn allowed() -> StartGate {
        StartGate {
            paired: true, owns_manager_lock: true,
            updating: false, shutting_down: false,
        }
    }

    #[test]
    fn automatic_start_requires_pairing_ownership_and_no_update_or_shutdown() {
        let now = Instant::now();
        for paired in [false, true] {
            for owns_manager_lock in [false, true] {
                for updating in [false, true] {
                    for shutting_down in [false, true] {
                        let mut supervisor = CamAgentSupervisor::new();
                        let gate = StartGate {
                            paired, owns_manager_lock, updating, shutting_down,
                        };
                        let result = supervisor.next_start(gate, false, now);
                        let expected = if paired && owns_manager_lock
                            && !updating && !shutting_down {
                            StartDecision::Start
                        } else {
                            StartDecision::Wait
                        };
                        assert_eq!(result, expected);
                    }
                }
            }
        }
    }

    #[test]
    fn gated_polls_do_not_consume_the_restart_budget() {
        let mut supervisor = CamAgentSupervisor::new();
        let now = Instant::now();
        let gate = StartGate { paired: false, ..allowed() };
        for _ in 0..20 {
            assert_eq!(supervisor.next_start(gate, false, now), StartDecision::Wait);
        }
        for _ in 0..4 {
            assert_eq!(supervisor.next_start(allowed(), false, now), StartDecision::Start);
            supervisor.launch_failed();
        }
        assert_eq!(supervisor.next_start(allowed(), false, now), StartDecision::Wait);
    }

    #[test]
    fn quick_exits_exhaust_the_budget_even_when_each_spawn_succeeds() {
        let mut supervisor = CamAgentSupervisor::new();
        let start = Instant::now();
        for attempt in 0..4 {
            let now = start + Duration::from_secs(attempt * 25);
            assert_eq!(supervisor.next_start(allowed(), false, now), StartDecision::Start);
            supervisor.started(now);
            assert_eq!(
                supervisor.next_start(allowed(), true, now + Duration::from_secs(1)),
                StartDecision::Wait
            );
        }
        assert_eq!(
            supervisor.next_start(allowed(), false, start + Duration::from_secs(100)),
            StartDecision::Paused
        );
        assert_eq!(
            supervisor.next_start(allowed(), false, start + Duration::from_secs(125)),
            StartDecision::Wait
        );
    }

    #[test]
    fn launch_errors_pause_at_four_and_manual_start_grants_a_fresh_budget() {
        let mut supervisor = CamAgentSupervisor::new();
        let now = Instant::now();
        for attempt in 1..=4 {
            assert_eq!(supervisor.next_start(allowed(), false, now), StartDecision::Start);
            assert_eq!(supervisor.launch_failed(), attempt == 4);
        }
        assert_eq!(supervisor.next_start(allowed(), false, now), StartDecision::Wait);
        supervisor.request_start();
        for _ in 0..4 {
            assert_eq!(supervisor.next_start(allowed(), false, now), StartDecision::Start);
            supervisor.launch_failed();
        }
        assert_eq!(supervisor.next_start(allowed(), false, now), StartDecision::Wait);
    }

    #[test]
    fn only_a_sustained_run_resets_previous_failed_attempts() {
        let mut supervisor = CamAgentSupervisor::new();
        let now = Instant::now();
        for _ in 0..3 {
            assert_eq!(supervisor.next_start(allowed(), false, now), StartDecision::Start);
            supervisor.launch_failed();
        }
        assert_eq!(supervisor.next_start(allowed(), false, now), StartDecision::Start);
        supervisor.started(now);
        assert_eq!(
            supervisor.next_start(allowed(), true, now + Duration::from_secs(59)),
            StartDecision::Wait
        );
        assert_eq!(supervisor.attempts, 4);
        assert_eq!(
            supervisor.next_start(allowed(), true, now + Duration::from_secs(60)),
            StartDecision::Wait
        );
        let later = now + Duration::from_secs(61);
        for _ in 0..4 {
            assert_eq!(supervisor.next_start(allowed(), false, later), StartDecision::Start);
            supervisor.launch_failed();
        }
        assert_eq!(supervisor.next_start(allowed(), false, later), StartDecision::Wait);
    }

    #[test]
    fn stop_wins_before_a_waiting_supervisor_acquires_the_lifecycle_lock() {
        let state = Arc::new(Mutex::new(CamAgentSupervisor::new()));
        let mut stopping = state.lock().unwrap();
        let queued = Arc::clone(&state);
        let (ready_tx, ready_rx) = mpsc::channel();
        let worker = thread::spawn(move || {
            ready_tx.send(()).unwrap();
            // No eligibility decision may be cached before acquiring this lock.
            queued.lock().unwrap().next_start(allowed(), false, Instant::now())
        });
        ready_rx.recv().unwrap();
        stopping.stop();
        drop(stopping);
        assert_eq!(worker.join().unwrap(), StartDecision::Wait);
        let mut supervisor = state.lock().unwrap();
        assert_eq!(
            supervisor.next_start(allowed(), false, Instant::now()),
            StartDecision::Wait
        );
        supervisor.request_start();
        assert_eq!(
            supervisor.next_start(allowed(), false, Instant::now()),
            StartDecision::Start
        );
    }
}
