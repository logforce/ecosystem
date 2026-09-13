// SPDX-License-Identifier: Apache-2.0
//! Small system-libc boundary for Unix peer identity, with no external Rust dependencies.
use std::{io, os::fd::AsRawFd, os::unix::net::UnixStream};

unsafe extern "C" {
    fn geteuid() -> u32;
}
pub fn effective_uid() -> u32 {
    unsafe { geteuid() }
}

#[cfg(target_os = "macos")]
pub fn peer_uid(stream: &UnixStream) -> io::Result<u32> {
    unsafe extern "C" {
        fn getpeereid(socket: i32, uid: *mut u32, gid: *mut u32) -> i32;
    }
    let (mut uid, mut gid) = (0, 0);
    if unsafe { getpeereid(stream.as_raw_fd(), &mut uid, &mut gid) } != 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(uid)
}

#[cfg(target_os = "linux")]
pub fn peer_uid(stream: &UnixStream) -> io::Result<u32> {
    #[repr(C)]
    struct Credentials {
        pid: i32,
        uid: u32,
        gid: u32,
    }
    unsafe extern "C" {
        fn getsockopt(
            fd: i32,
            level: i32,
            name: i32,
            value: *mut std::ffi::c_void,
            len: *mut u32,
        ) -> i32;
    }
    let mut credentials = Credentials {
        pid: 0,
        uid: 0,
        gid: 0,
    };
    let mut len = std::mem::size_of::<Credentials>() as u32;
    // Linux SOL_SOCKET=1, SO_PEERCRED=17; ucred/socklen_t layout on the supported Linux target.
    let result = unsafe {
        getsockopt(
            stream.as_raw_fd(),
            1,
            17,
            (&mut credentials as *mut Credentials).cast(),
            &mut len,
        )
    };
    if result != 0 {
        return Err(io::Error::last_os_error());
    }
    if len as usize != std::mem::size_of::<Credentials>() {
        return Err(io::Error::other("invalid peer credentials"));
    }
    Ok(credentials.uid)
}
