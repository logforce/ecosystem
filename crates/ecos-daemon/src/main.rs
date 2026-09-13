// SPDX-License-Identifier: Apache-2.0
use std::{
    io::{self, BufRead},
    path::PathBuf,
    sync::atomic::{AtomicBool, Ordering},
    time::Duration,
};

static STOP: AtomicBool = AtomicBool::new(false);
extern "C" fn stop_signal(_: i32) {
    STOP.store(true, Ordering::Relaxed);
}

fn install_signals() -> io::Result<()> {
    unsafe extern "C" {
        fn signal(number: i32, handler: extern "C" fn(i32)) -> usize;
    }
    for number in [2, 15] {
        if unsafe { signal(number, stop_signal) } == usize::MAX {
            return Err(io::Error::last_os_error());
        }
    }
    Ok(())
}

fn main() {
    if let Err(error) = run() {
        eprintln!("ecosd: {error}");
        std::process::exit(1);
    }
}
fn run() -> io::Result<()> {
    let mut args = std::env::args().skip(1);
    let mut socket = None;
    let mut stdin_shutdown = false;
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--socket" => socket = args.next().map(PathBuf::from),
            "--shutdown-on-stdin" => stdin_shutdown = true,
            "--help" => {
                println!(
                    "ecosd --socket /private/runtime-directory/ecos.sock [--shutdown-on-stdin]"
                );
                return Ok(());
            }
            _ => return Err(io::Error::other("unknown argument; use --help")),
        }
    }
    let socket = socket.ok_or_else(|| io::Error::other("--socket is required"))?;
    if !stdin_shutdown {
        install_signals()?;
    }
    let _server = ecos_daemon::Server::bind(&socket)?;
    println!(
        "READY {} (mock backend; experimental protocol {}.{})",
        socket.display(),
        cog_protocol::MAJOR,
        cog_protocol::MINOR
    );
    if stdin_shutdown {
        let mut line = String::new();
        io::stdin().lock().read_line(&mut line)?;
    } else {
        while !STOP.load(Ordering::Relaxed) {
            std::thread::sleep(Duration::from_millis(20));
        }
    }
    Ok(())
}
