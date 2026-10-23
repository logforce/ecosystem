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

fn value(args: &mut impl Iterator<Item = String>, option: &str) -> io::Result<String> {
    args.next()
        .filter(|value| !value.starts_with("--"))
        .ok_or_else(|| io::Error::other(format!("{option} requires a value")))
}

fn run() -> io::Result<()> {
    let mut args = std::env::args().skip(1);
    let mut socket = None;
    let mut stdin_shutdown = false;
    let mut worker_python = None;
    let mut worker_script = None;
    let mut model = None;
    let mut etex_requested = false;
    let mut etex_python = None;
    let mut etex_worker = None;
    let mut etex_engine: Option<String> = None;
    let mut etex_snapshot = None;
    let mut etex_encoder = None;
    let mut etex_reranker = None;
    let mut etex_root = None;
    let mut etex_model_file = "model.fused.int8.full.onnx".to_owned();
    let mut etex_reranker_model_file = "model.fused.int8.full.onnx".to_owned();
    let mut etex_threads = 1usize;
    let mut etex_strategy = "auto".to_owned();
    let mut etex_timeout_ms = 120_000u64;
    let mut etex_no_reranker = false;
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--socket" => socket = Some(PathBuf::from(value(&mut args, &arg)?)),
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
            "--etex-python"
            | "--etex-worker"
            | "--etex-snapshot"
            | "--etex-encoder-dir"
            | "--etex-reranker-dir"
            | "--etex-root" => {
                etex_requested = true;
                let path = Some(PathBuf::from(value(&mut args, &arg)?));
                match arg.as_str() {
                    "--etex-python" => etex_python = path,
                    "--etex-worker" => etex_worker = path,
                    "--etex-snapshot" => etex_snapshot = path,
                    "--etex-encoder-dir" => etex_encoder = path,
                    "--etex-reranker-dir" => etex_reranker = path,
                    _ => etex_root = path,
                }
            }
            "--etex-engine"
            | "--etex-model-file"
            | "--etex-reranker-model-file"
            | "--etex-threads"
            | "--etex-strategy"
            | "--etex-timeout-ms" => {
                etex_requested = true;
                let argument = value(&mut args, &arg)?;
                match arg.as_str() {
                    "--etex-engine" => {
                        if argument != "fixture" && argument != "etex" {
                            return Err(io::Error::other("--etex-engine must be fixture or etex"));
                        }
                        etex_engine = Some(argument);
                    }
                    "--etex-model-file" => etex_model_file = argument,
                    "--etex-reranker-model-file" => etex_reranker_model_file = argument,
                    "--etex-threads" => {
                        etex_threads = argument
                            .parse()
                            .map_err(|_| io::Error::other("--etex-threads requires an integer"))?
                    }
                    "--etex-timeout-ms" => {
                        etex_timeout_ms = argument.parse().map_err(|_| {
                            io::Error::other("--etex-timeout-ms requires an integer")
                        })?
                    }
                    _ => etex_strategy = argument,
                }
            }
            "--etex-no-reranker" => {
                etex_requested = true;
                etex_no_reranker = true;
            }
            "--help" => {
                println!(
                    "ecosd --socket PATH [--shutdown-on-stdin]\n\
Optional MNIST worker: --worker-python PATH --worker-script PATH --mnist-model PATH\n\
Optional ETE-X worker (mutually exclusive with MNIST):\n\
  --etex-python ABSOLUTE_PATH --etex-worker PATH --etex-engine fixture|etex\n\
  ETE-X mode: --etex-snapshot DIR --etex-encoder-dir DIR --etex-reranker-dir DIR\n\
  Options: --etex-root DIR --etex-threads 1..64 --etex-strategy auto|raw|bounded\n\
           --etex-model-file BASENAME --etex-reranker-model-file BASENAME\n\
           --etex-no-reranker --etex-timeout-ms 1..3600000 (default 120000)\n\
  Build with --features etex to enable this separately licensed component.\n\
  Fixture mode is explicit and executes no AI. Capability: etex.rag.search.v1\n\
  Trusted operator paths only. This supervisor is not a worker security sandbox."
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
    if configured && etex_requested {
        return Err(io::Error::other(
            "MNIST and ETE-X backends are mutually exclusive",
        ));
    }
    let backend_name = if etex_requested {
        if etex_engine.as_deref() == Some("fixture") {
            "ETE-X fixture, no AI"
        } else {
            "ETE-X worker"
        }
    } else if configured {
        "MNIST worker"
    } else {
        "mock"
    };
    let _server = if etex_requested {
        #[cfg(all(target_os = "linux", feature = "etex"))]
        {
            use cog_backend_process::{EteXConfig, EteXEngine, EteXProcessBackend};
            let engine = match etex_engine.as_deref() {
                Some("fixture") => EteXEngine::Fixture,
                Some("etex") => EteXEngine::EteX,
                _ => {
                    return Err(io::Error::other(
                        "--etex-engine is required; fixture mode is never implicit",
                    ))
                }
            };
            let mut config = EteXConfig::new(
                etex_python.ok_or_else(|| io::Error::other("--etex-python is required"))?,
                etex_worker.ok_or_else(|| io::Error::other("--etex-worker is required"))?,
                engine,
            );
            config.snapshot = etex_snapshot;
            config.encoder_dir = etex_encoder;
            config.reranker_dir = etex_reranker;
            config.ete_x_root = etex_root;
            config.model_file = etex_model_file;
            config.reranker_model_file = etex_reranker_model_file;
            config.threads = etex_threads;
            config.strategy = etex_strategy;
            config.no_reranker = etex_no_reranker;
            config.timeout = Duration::from_millis(etex_timeout_ms);
            let backend = EteXProcessBackend::new(config)?;
            ecos_daemon::Server::with_backend(&socket, std::sync::Arc::new(backend))?
        }
        #[cfg(not(all(target_os = "linux", feature = "etex")))]
        {
            let _ = (
                etex_python,
                etex_worker,
                etex_engine,
                etex_snapshot,
                etex_encoder,
                etex_reranker,
                etex_root,
                etex_model_file,
                etex_reranker_model_file,
                etex_threads,
                etex_strategy,
                etex_timeout_ms,
                etex_no_reranker,
            );
            return Err(io::Error::other("ETE-X requires Linux and an explicit build with --features etex; see components/ete-x/LICENSE.md"));
        }
    } else if configured {
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
        backend_name,
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
