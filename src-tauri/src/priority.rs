//! The priority encodes run at. Lower, other programs get the processor first when they
//! need it; when they don't, an encode still uses all of it.

use serde::{Deserialize, Serialize};

/// Windows' priority classes, but Realtime: without administrator rights Windows gives
/// High instead, and with them an encode could freeze the computer.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum Priority {
    Idle,
    #[default]
    BelowNormal,
    Normal,
    AboveNormal,
    High,
}

/// Encodes run at their priority while one of these is alive.
pub struct Raised(#[allow(dead_code)] os::State);

/// Sets `priority` for the encodes of the calling thread until the result is dropped.
pub fn enter(priority: Priority) -> Raised {
    Raised(os::enter(priority))
}

#[cfg(windows)]
mod os {
    use std::ffi::c_void;
    use std::sync::Mutex;

    use super::Priority;

    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn GetCurrentProcess() -> *mut c_void;
        fn SetPriorityClass(process: *mut c_void, class: u32) -> i32;
    }

    const NORMAL: u32 = 0x20;

    /// Encodes running: the encoders' threads are FFmpeg's, made at normal priority within
    /// the process's class, so the class is what changes, back to normal after the last one.
    static RUNNING: Mutex<usize> = Mutex::new(0);

    fn set(class: u32) {
        unsafe { SetPriorityClass(GetCurrentProcess(), class) };
    }

    pub struct State;

    pub fn enter(priority: Priority) -> State {
        let mut running = RUNNING.lock().unwrap_or_else(|e| e.into_inner());
        *running += 1;
        set(match priority {
            Priority::Idle => 0x40,
            Priority::BelowNormal => 0x4000,
            Priority::Normal => NORMAL,
            Priority::AboveNormal => 0x8000,
            Priority::High => 0x80,
        });
        State
    }

    impl Drop for State {
        fn drop(&mut self) {
            let mut running = RUNNING.lock().unwrap_or_else(|e| e.into_inner());
            *running -= 1;
            if *running == 0 {
                set(NORMAL);
            }
        }
    }
}

#[cfg(target_os = "android")]
mod os {
    use std::ffi::c_int;

    use super::Priority;

    unsafe extern "C" {
        fn gettid() -> c_int;
        fn getpriority(which: c_int, who: u32) -> c_int;
        fn setpriority(which: c_int, who: u32, nice: c_int) -> c_int;
    }

    const PRIO_PROCESS: c_int = 0;

    /// Linux sets the priority ("nice") of each thread, and the threads a thread starts take
    /// its own: set on the job's thread before the encoders start theirs. An app may not be
    /// allowed the highest ones (under 0); the thread then keeps its own.
    pub struct State {
        thread: u32,
        before: c_int,
    }

    pub fn enter(priority: Priority) -> State {
        let thread = unsafe { gettid() } as u32;
        let before = unsafe { getpriority(PRIO_PROCESS, thread) };
        let nice = match priority {
            Priority::Idle => 19,
            Priority::BelowNormal => 10,
            Priority::Normal => 0,
            Priority::AboveNormal => -5,
            Priority::High => -10,
        };
        unsafe { setpriority(PRIO_PROCESS, thread, nice) };
        State { thread, before }
    }

    impl Drop for State {
        fn drop(&mut self) {
            unsafe { setpriority(PRIO_PROCESS, self.thread, self.before) };
        }
    }
}

#[cfg(not(any(windows, target_os = "android")))]
mod os {
    pub struct State;

    pub fn enter(_: super::Priority) -> State {
        State
    }
}

#[cfg(all(test, windows))]
mod tests {
    use super::*;

    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn GetPriorityClass(process: *mut std::ffi::c_void) -> u32;
        fn GetCurrentProcess() -> *mut std::ffi::c_void;
    }

    fn class() -> u32 {
        unsafe { GetPriorityClass(GetCurrentProcess()) }
    }

    #[test]
    fn back_to_normal_after_the_last_encode() {
        let first = enter(Priority::Idle);
        assert_eq!(class(), 0x40);
        let second = enter(Priority::BelowNormal);
        assert_eq!(class(), 0x4000);
        drop(first);
        assert_eq!(class(), 0x4000);
        drop(second);
        assert_eq!(class(), 0x20);
    }
}
