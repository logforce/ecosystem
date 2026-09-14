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
    let mut worker_python = None;
    let mut worker_script = None;
    let mut model = None;
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--socket" => socket = args.next().map(PathBuf::from),
            "--shutdown-on-stdin" => stdin_shutdown = true,
            "--worker-python" | "--worker-script" | "--mnist-model" => {
                let value = args
                    .next()
                    .filter(|value| !value.starts_with("--"))
                    .ok_or_else(|| io::Error::other(format!("{arg} requires a path")))?;
                let path = Some(PathBuf::from(value));
                match arg.as_str() {
                    "--worker-python" => worker_python = path,
                    "--worker-script" => worker_script = path,
                    _ => model = path,
                }
            }
            "--help" => {
                println!(
                    "ecosd --socket PATH [--shutdown-on-stdin]\nOptional MNIST worker: --worker-python PATH --worker-script PATH --mnist-model PATH"
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
    let configured = worker_python.is_some() || worker_script.is_some() || model.is_some();
    let _server = if configured {
        #[cfg(target_os = "linux")]
        {
            let missing = || io::Error::other("all three worker paths are required");
            let backend = cog_backend_process::ProcessBackend::new(
                worker_python.ok_or_else(missing)?,
                worker_script.ok_or_else(missing)?,
                model.ok_or_else(missing)?,
            )?;
            ecos_daemon::Server::with_backend(&socket, std::sync::Arc::new(backend))?
        }
        #[cfg(not(target_os = "linux"))]
        {
            return Err(io::Error::other("ONNX worker requires Linux"));
        }
    } else {
        ecos_daemon::Server::bind(&socket)?
    };
    println!(
        "READY {} ({} backend; experimental protocol {}.{})",
        socket.display(),
        if configured { "MNIST worker" } else { "mock" },
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
