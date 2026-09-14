// SPDX-License-Identifier: Apache-2.0
use cog_backend_api::Backend;
use cog_core::{Error, JobStatus, MAX_BUFFER, MAX_OBJECTS, MAX_SESSIONS};
use cog_protocol::{Frame, Request};
use cogposix::{Client, TensorDesc};
use ecos_daemon::Server;
use std::{
    fs,
    io::Read,
    os::unix::{fs::PermissionsExt, net::UnixStream},
    path::PathBuf,
    process::{Command, Stdio},
    sync::{
        atomic::{AtomicBool, AtomicU64, Ordering},
        Arc,
    },
    thread,
    time::{Duration, Instant},
};

static NEXT: AtomicU64 = AtomicU64::new(1);
struct Fixture {
    server: Option<Server>,
    dir: PathBuf,
    socket: PathBuf,
}
impl Fixture {
    fn new() -> Self {
        Self::backend(Arc::new(cog_backend_mock::MockBackend))
    }
    fn backend(backend: Arc<dyn Backend>) -> Self {
        let dir = std::env::temp_dir().join(format!(
            "cog-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        let socket = dir.join("s");
        let server = Server::with_backend(&socket, backend).unwrap();
        Self {
            server: Some(server),
            dir,
            socket,
        }
    }
    fn client(&self) -> Client {
        Client::connect(&self.socket).unwrap()
    }
    fn empty(&self) {
        until(|| {
            let m = self.server.as_ref().unwrap().metrics();
            m.sessions == 0 && m.objects == 0 && m.live_bytes == 0
        });
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        self.server.take();
        let _ = fs::remove_dir_all(&self.dir);
    }
}
fn until(mut condition: impl FnMut() -> bool) {
    let deadline = Instant::now() + Duration::from_secs(5);
    while !condition() {
        assert!(Instant::now() < deadline, "condition timed out");
        thread::sleep(Duration::from_millis(2));
    }
}
struct Work {
    model: u64,
    input: u64,
    output: u64,
    a: u64,
    b: u64,
}
fn work(client: &mut Client) -> Work {
    let model = client.model_open("mock.increment.v1").unwrap();
    let input = client.buffer_alloc(4).unwrap();
    let output = client.buffer_alloc(4).unwrap();
    client.buffer_write(input, &[0, 1, 254, 255]).unwrap();
    let desc = TensorDesc {
        offset: 0,
        shape: vec![2, 2],
    };
    let a = client.tensor_create(input, desc.clone()).unwrap();
    let b = client.tensor_create(output, desc).unwrap();
    Work {
        model,
        input,
        output,
        a,
        b,
    }
}

#[test]
fn two_independent_clients_execute_and_cannot_borrow_handles() {
    let f = Fixture::new();
    let (mut a, mut b) = (f.client(), f.client());
    let w = work(&mut a);
    assert_eq!(b.buffer_read(w.input), Err(Error::Invalid));
    assert_eq!(a.buffer_read(w.model), Err(Error::Invalid));
    assert_eq!(a.model_open("/tmp/arbitrary-model"), Err(Error::NoModel));
    let job = a.submit(w.model, w.a, w.b, 0).unwrap();
    let z = work(&mut b);
    let other = b.submit(z.model, z.a, z.b, 0).unwrap();
    assert_eq!(
        a.wait(job, Duration::from_secs(2)),
        Ok(JobStatus::Completed)
    );
    assert_eq!(
        b.wait(other, Duration::from_secs(2)),
        Ok(JobStatus::Completed)
    );
    assert_eq!(a.buffer_read(w.output).unwrap(), vec![1, 2, 255, 0]);
    assert_eq!(b.buffer_read(z.output).unwrap(), vec![1, 2, 255, 0]);
    a.job_release(job).unwrap();
    assert_eq!(a.job_status(job), Err(Error::Invalid));
    drop(a);
    drop(b);
    f.empty();
}

#[test]
fn tensor_and_job_references_retain_closed_resources() {
    let f = Fixture::new();
    let mut c = f.client();
    let w = work(&mut c);
    c.buffer_free(w.input).unwrap();
    assert_eq!(c.buffer_read(w.input), Err(Error::Invalid));
    assert_eq!(c.stats().unwrap().live_bytes, 8);
    let job = c.submit(w.model, w.a, w.b, 20).unwrap();
    c.model_close(w.model).unwrap();
    c.tensor_release(w.a).unwrap();
    c.tensor_release(w.b).unwrap();
    c.wait(job, Duration::from_secs(2)).unwrap();
    assert_eq!(c.buffer_read(w.output).unwrap(), vec![1, 2, 255, 0]);
    assert_eq!(c.stats().unwrap().live_bytes, 4);
    c.buffer_free(w.output).unwrap();
    c.job_release(job).unwrap();
    assert_eq!(c.stats().unwrap().objects, 0);
    assert_eq!(c.stats().unwrap().live_bytes, 0);
    drop(c);
    f.empty();
}

#[test]
fn running_cancellation_preserves_lifetimes_and_wait_timeout_is_not_cancellation() {
    let f = Fixture::new();
    let mut c = f.client();
    let w = work(&mut c);
    let job = c.submit(w.model, w.a, w.b, 5000).unwrap();
    until(|| c.job_status(job).unwrap().0 == JobStatus::Running);
    assert_eq!(c.wait(job, Duration::ZERO), Err(Error::Timeout));
    assert_eq!(c.buffer_write(w.input, &[9; 4]), Err(Error::Busy));
    assert_eq!(c.buffer_read(w.output), Err(Error::Busy));
    assert_eq!(c.job_release(job), Err(Error::Busy));
    c.job_cancel(job).unwrap();
    assert_eq!(c.wait(job, Duration::from_secs(2)), Err(Error::Cancelled));
    assert_eq!(c.buffer_read(w.output).unwrap(), vec![0; 4]);
    c.job_cancel(job).unwrap();
    c.job_release(job).unwrap();
    let next = c.submit(w.model, w.a, w.b, 0).unwrap();
    c.wait(next, Duration::from_secs(2)).unwrap();
    assert_eq!(c.buffer_read(w.output).unwrap(), vec![1, 2, 255, 0]);
}

#[test]
fn queued_job_cancels_without_waiting_for_running_work() {
    let f = Fixture::new();
    let (mut a, mut b) = (f.client(), f.client());
    let first = work(&mut a);
    let second = work(&mut b);
    let running = a.submit(first.model, first.a, first.b, 5000).unwrap();
    until(|| a.job_status(running).unwrap().0 == JobStatus::Running);
    let queued = b.submit(second.model, second.a, second.b, 0).unwrap();
    assert_eq!(b.job_status(queued).unwrap().0, JobStatus::Queued);
    b.job_cancel(queued).unwrap();
    assert_eq!(b.job_status(queued).unwrap().0, JobStatus::Cancelled);
    b.buffer_write(second.input, &[3; 4]).unwrap();
    a.job_cancel(running).unwrap();
}

#[test]
fn stale_handles_and_overflowing_or_aliased_tensors_are_rejected() {
    let f = Fixture::new();
    let mut c = f.client();
    let w = work(&mut c);
    for desc in [
        TensorDesc {
            offset: u64::MAX,
            shape: vec![1],
        },
        TensorDesc {
            offset: 0,
            shape: vec![u64::MAX, 2],
        },
    ] {
        assert_eq!(c.tensor_create(w.input, desc), Err(Error::Invalid));
    }
    assert_eq!(c.submit(w.model, w.a, w.a, 0), Err(Error::Invalid));
    c.tensor_release(w.a).unwrap();
    let new = c
        .tensor_create(
            w.input,
            TensorDesc {
                offset: 0,
                shape: vec![4],
            },
        )
        .unwrap();
    assert_ne!(new, w.a);
    assert_eq!(c.submit(w.model, w.a, w.b, 0), Err(Error::Invalid));
    assert_eq!(c.submit(w.model, new, w.b, 0), Err(Error::Invalid));
}

#[test]
fn memory_objects_and_sessions_are_bounded() {
    let f = Fixture::new();
    let mut c = f.client();
    for _ in 0..8 {
        c.buffer_alloc(MAX_BUFFER).unwrap();
    }
    assert_eq!(c.buffer_alloc(1), Err(Error::NoMemory));
    drop(c);
    f.empty();
    let mut c = f.client();
    for _ in 0..MAX_OBJECTS {
        c.model_open("mock.increment.v1").unwrap();
    }
    assert_eq!(c.model_open("mock.increment.v1"), Err(Error::Busy));
    drop(c);
    f.empty();
    let clients: Vec<_> = (0..MAX_SESSIONS).map(|_| f.client()).collect();
    assert!(Client::connect(&f.socket).is_err());
    drop(clients);
    f.empty();
}

#[test]
fn global_memory_limit_counts_tensor_retained_buffers_and_recovers() {
    let f = Fixture::new();
    let mut clients = Vec::new();
    let mut retained = Vec::new();
    for _ in 0..cog_core::GLOBAL_MEMORY / cog_core::SESSION_MEMORY {
        let mut c = f.client();
        for _ in 0..cog_core::SESSION_MEMORY / MAX_BUFFER {
            let buffer = c.buffer_alloc(MAX_BUFFER).unwrap();
            let tensor = c
                .tensor_create(
                    buffer,
                    TensorDesc {
                        offset: 0,
                        shape: vec![1],
                    },
                )
                .unwrap();
            c.buffer_free(buffer).unwrap();
            retained.push(tensor);
        }
        assert_eq!(
            c.stats().unwrap().live_bytes,
            cog_core::SESSION_MEMORY as u64
        );
        clients.push(c);
    }
    let mut extra = f.client();
    assert_eq!(extra.buffer_alloc(1), Err(Error::NoMemory));
    assert_eq!(
        f.server.as_ref().unwrap().metrics().live_bytes,
        cog_core::GLOBAL_MEMORY
    );
    clients[0].tensor_release(retained[0]).unwrap();
    let replacement = extra.buffer_alloc(MAX_BUFFER).unwrap();
    assert_eq!(extra.buffer_alloc(1), Err(Error::NoMemory));
    extra.buffer_free(replacement).unwrap();
    drop(extra);
    drop(clients);
    f.empty();
}

#[test]
fn cancellation_completion_stress_never_commits_cancelled_output() {
    let f = Fixture::new();
    let threads: Vec<_> = (0..4)
        .map(|client_index| {
            let socket = f.socket.clone();
            thread::spawn(move || {
                let mut c = Client::connect(socket).unwrap();
                let w = work(&mut c);
                for iteration in 0..64 {
                    c.buffer_write(w.output, &[73; 4]).unwrap();
                    let job = c.submit(w.model, w.a, w.b, iteration % 3).unwrap();
                    match (iteration + client_index) % 3 {
                        0 => thread::yield_now(),
                        1 => thread::sleep(Duration::from_millis(1)),
                        _ => thread::sleep(Duration::from_millis(3)),
                    }
                    c.job_cancel(job).unwrap();
                    let result = c.wait(job, Duration::from_secs(5));
                    let bytes = c.buffer_read(w.output).unwrap();
                    match result {
                        Ok(JobStatus::Completed) => assert_eq!(bytes, vec![1, 2, 255, 0]),
                        Err(Error::Cancelled) => assert_eq!(bytes, vec![73; 4]),
                        other => panic!("unexpected terminal result: {other:?}"),
                    }
                    let terminal = c.job_status(job).unwrap();
                    c.job_cancel(job).unwrap();
                    assert_eq!(c.job_status(job).unwrap(), terminal);
                    c.job_release(job).unwrap();
                    assert_eq!(c.stats().unwrap().jobs, 0);
                }
            })
        })
        .collect();
    for thread in threads {
        thread.join().unwrap();
    }
    f.empty();
}

#[test]
fn malformed_and_ungreeted_connections_are_reclaimed() {
    let f = Fixture::new();
    for (op, body) in [(20, 4u32.to_le_bytes().to_vec()), (1, vec![0])] {
        let mut stream = UnixStream::connect(&f.socket).unwrap();
        stream
            .set_read_timeout(Some(Duration::from_secs(2)))
            .unwrap();
        Frame {
            id: 1,
            opcode: op,
            flags: 0,
            status: 0,
            body,
        }
        .write(&mut stream)
        .unwrap();
        let mut byte = [0];
        assert_eq!(stream.read(&mut byte).unwrap(), 0);
    }
    f.empty();
    let mut c = f.client();
    assert_eq!(c.stats().unwrap().objects, 0);
}

#[test]
fn protocol_errors_are_explicit_without_corrupting_session() {
    let f = Fixture::new();
    let mut c = f.client();
    assert_eq!(c.request(Request::BufferAlloc(0)), Err(Error::Invalid));
    assert_eq!(c.stats().unwrap().objects, 0);
    let mut stream = UnixStream::connect(&f.socket).unwrap();
    stream
        .set_read_timeout(Some(Duration::from_secs(2)))
        .unwrap();
    Frame {
        id: 1,
        opcode: 1,
        flags: 0,
        status: 0,
        body: vec![],
    }
    .write(&mut stream)
    .unwrap();
    Frame::read(&mut stream).unwrap();
    Frame {
        id: 2,
        opcode: 999,
        flags: 0,
        status: 0,
        body: vec![],
    }
    .write(&mut stream)
    .unwrap();
    assert_eq!(
        Frame::read(&mut stream).unwrap().status,
        Error::Unsupported as i32
    );
    Frame {
        id: 2,
        opcode: 1,
        flags: 0,
        status: 0,
        body: vec![],
    }
    .write(&mut stream)
    .unwrap();
    let mut byte = [0];
    assert_eq!(stream.read(&mut byte).unwrap(), 0);
}

struct BrokenBackend;
impl Backend for BrokenBackend {
    fn supports(&self, _: &str) -> bool {
        true
    }
    fn execute(&self, _: &[u8], _: u32, _: &AtomicBool) -> cog_core::Result<Vec<u8>> {
        panic!("intentional mock failure")
    }
}
#[test]
fn backend_panic_fails_job_without_killing_coordinator() {
    let f = Fixture::backend(Arc::new(BrokenBackend));
    let mut c = f.client();
    let w = work(&mut c);
    for _ in 0..2 {
        let job = c.submit(w.model, w.a, w.b, 0).unwrap();
        assert_eq!(c.wait(job, Duration::from_secs(2)), Err(Error::Backend));
        c.job_release(job).unwrap();
    }
    assert_eq!(c.stats().unwrap().jobs, 0);
}

#[test]
fn bind_refuses_existing_paths_and_public_directories() {
    let f = Fixture::new();
    assert!(Server::bind(&f.socket).is_err());
    let existing = f.dir.join("important");
    fs::write(&existing, "keep").unwrap();
    assert!(Server::bind(&existing).is_err());
    assert_eq!(fs::read_to_string(&existing).unwrap(), "keep");
    let public = f.dir.join("public");
    fs::create_dir(&public).unwrap();
    fs::set_permissions(&public, fs::Permissions::from_mode(0o755)).unwrap();
    assert!(Server::bind(public.join("s")).is_err());
}

#[test]
fn server_shutdown_disconnects_clients_and_cleans_socket() {
    let mut f = Fixture::new();
    let mut c = f.client();
    let w = work(&mut c);
    c.submit(w.model, w.a, w.b, 5000).unwrap();
    f.server.take();
    assert!(!f.socket.exists());
    assert_eq!(c.stats(), Err(Error::Disconnected));
}

#[test]
fn shutdown_does_not_remove_a_replacement_file() {
    let mut f = Fixture::new();
    fs::remove_file(&f.socket).unwrap();
    fs::write(&f.socket, "replacement owned by user").unwrap();
    f.server.take();
    assert_eq!(
        fs::read_to_string(&f.socket).unwrap(),
        "replacement owned by user"
    );
}

#[test]
fn job_admission_is_bounded_even_for_tiny_buffers() {
    let f = Fixture::new();
    let mut c = f.client();
    let mut jobs = Vec::new();
    for _ in 0..cog_core::MAX_JOBS {
        let w = work(&mut c);
        jobs.push(c.submit(w.model, w.a, w.b, 5000).unwrap());
    }
    let extra = work(&mut c);
    assert_eq!(c.submit(extra.model, extra.a, extra.b, 0), Err(Error::Busy));
    for job in jobs {
        c.job_cancel(job).unwrap();
    }
    drop(c);
    f.empty();
}

#[test]
#[ignore = "subprocess helper, exercised by killed_client_reclaims_resources"]
fn crash_child() {
    let socket = std::env::var("COG_TEST_SOCKET").unwrap();
    let mut c = Client::connect(socket).unwrap();
    let w = work(&mut c);
    #[allow(unused_mut)]
    let mut input = w.a;
    #[cfg(target_os = "linux")]
    if std::env::var_os("COG_TEST_SHARED").is_some() {
        let shared = cogposix::SharedBuffer::from_bytes(&[1; 4]).unwrap();
        let buffer = c.buffer_import_file(shared.file(), 4).unwrap();
        input = c
            .tensor_create(
                buffer,
                TensorDesc {
                    offset: 0,
                    shape: vec![2, 2],
                },
            )
            .unwrap();
        c.buffer_free(buffer).unwrap();
    }
    c.submit(w.model, input, w.b, 5000).unwrap();
    fs::write(std::env::var("COG_TEST_READY").unwrap(), "ready").unwrap();
    loop {
        thread::sleep(Duration::from_secs(1));
    }
}

#[test]
fn killed_client_reclaims_resources() {
    let f = Fixture::new();
    kill_client(&f, false);
    #[cfg(target_os = "linux")]
    kill_client(&f, true);
}

fn kill_client(f: &Fixture, shared: bool) {
    let ready = f.dir.join(format!("ready-{shared}"));
    let mut command = Command::new(std::env::current_exe().unwrap());
    if shared {
        command.env("COG_TEST_SHARED", "1");
    } else {
        command.env_remove("COG_TEST_SHARED");
    }
    let mut child = command
        .args(["--exact", "crash_child", "--ignored"])
        .env("COG_TEST_SOCKET", &f.socket)
        .env("COG_TEST_READY", &ready)
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .unwrap();
    let deadline = Instant::now() + Duration::from_secs(5);
    while !ready.exists() && Instant::now() < deadline {
        thread::sleep(Duration::from_millis(2));
    }
    let was_ready = ready.exists();
    let _ = child.kill();
    child.wait().unwrap();
    assert!(was_ready, "crash helper did not start");
    f.empty();
}

#[cfg(target_os = "linux")]
#[test]
fn shared_import_inference_and_export_preserve_ownership() {
    let f = Fixture::new();
    let mut c = f.client();
    let shared = cogposix::SharedBuffer::from_bytes(&[0, 1, 254, 255]).unwrap();
    assert_ne!(c.features() & cog_protocol::FEATURE_SEALED_SHM, 0);
    let input = c.buffer_import_file(shared.file(), 4).unwrap();
    drop(shared);
    let output = c.buffer_alloc(4).unwrap();
    let model = c.model_open("mock.increment.v1").unwrap();
    let desc = TensorDesc {
        offset: 0,
        shape: vec![4],
    };
    let a = c.tensor_create(input, desc.clone()).unwrap();
    let b = c.tensor_create(output, desc).unwrap();
    assert_eq!(c.buffer_write(input, &[8; 4]), Err(Error::Permission));
    assert_eq!(c.submit(model, b, a, 0), Err(Error::Permission));
    let job = c.submit(model, a, b, 20).unwrap();
    assert!(matches!(c.buffer_export_file(output), Err(Error::Busy)));
    c.buffer_free(input).unwrap();
    c.tensor_release(a).unwrap();
    c.wait(job, Duration::from_secs(5)).unwrap();
    assert_eq!(c.stats().unwrap().live_bytes, 4);
    let (file, size) = c.buffer_export_file(output).unwrap();
    let snapshot = cogposix::SharedBuffer::from_file(file, size).unwrap();
    c.buffer_write(output, &[7; 4]).unwrap();
    drop(c);
    f.empty();
    assert_eq!(snapshot.bytes(), &[1, 2, 255, 0]);
    assert!(snapshot.file().set_len(1).is_err());
}

#[cfg(target_os = "linux")]
#[test]
fn shared_import_validation_and_disconnect_reclaim_quota() {
    let f = Fixture::new();
    let mut c = f.client();
    let shared = cogposix::SharedBuffer::from_bytes(&vec![1; MAX_BUFFER]).unwrap();
    assert_eq!(c.buffer_import_file(shared.file(), 1), Err(Error::Invalid));
    let path = f.dir.join("ordinary-file");
    fs::write(&path, [1; 4]).unwrap();
    assert_eq!(
        c.buffer_import_file(&fs::File::open(path).unwrap(), 4),
        Err(Error::Permission)
    );
    assert_eq!(c.request(Request::BufferImport(4)), Err(Error::Invalid));
    assert_eq!(c.stats().unwrap().live_bytes, 0);
    for _ in 0..8 {
        c.buffer_import_file(shared.file(), MAX_BUFFER).unwrap();
    }
    assert_eq!(
        c.buffer_import_file(shared.file(), MAX_BUFFER),
        Err(Error::NoMemory)
    );
    drop(c);
    f.empty();
}

#[cfg(target_os = "linux")]
#[test]
fn shared_cancellation_does_not_commit_output() {
    let f = Fixture::new();
    let mut c = f.client();
    let shared = cogposix::SharedBuffer::from_bytes(&[2; 4]).unwrap();
    let w = work(&mut c);
    let input = c.buffer_import_file(shared.file(), 4).unwrap();
    let a = c
        .tensor_create(
            input,
            TensorDesc {
                offset: 0,
                shape: vec![2, 2],
            },
        )
        .unwrap();
    let job = c.submit(w.model, a, w.b, 5000).unwrap();
    until(|| c.job_status(job).unwrap().0 == JobStatus::Running);
    c.buffer_free(input).unwrap();
    c.tensor_release(a).unwrap();
    c.job_cancel(job).unwrap();
    assert_eq!(c.wait(job, Duration::from_secs(5)), Err(Error::Cancelled));
    let (file, size) = c.buffer_export_file(w.output).unwrap();
    assert_eq!(
        cogposix::SharedBuffer::from_file(file, size)
            .unwrap()
            .bytes(),
        &[0; 4]
    );
    drop(c);
    f.empty();
}

#[cfg(target_os = "linux")]
#[test]
fn descriptors_on_wrong_requests_close_the_session() {
    let f = Fixture::new();
    let shared = cogposix::SharedBuffer::from_bytes(&[1]).unwrap();
    let mut socket = UnixStream::connect(&f.socket).unwrap();
    socket
        .set_read_timeout(Some(Duration::from_secs(2)))
        .unwrap();
    Frame {
        id: 1,
        opcode: 1,
        flags: 0,
        status: 0,
        body: vec![],
    }
    .write_socket(&mut socket, Some(shared.file()))
    .unwrap();
    assert!(Frame::read_socket(&mut socket).is_err());
    f.empty();
}
