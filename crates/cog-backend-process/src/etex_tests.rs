// ETE-X handoff integration tests. Fixture workers do not execute AI models.
// SPDX-License-Identifier: CC-BY-NC-ND-4.0
// ETE-X integration tests; see components/ete-x/LICENSE.md.
use super::*;
use std::{
    fs,
    sync::{atomic::AtomicU64, Arc},
};

static NEXT: AtomicU64 = AtomicU64::new(0);
const SUPPORT: &str = r#"
import sys, struct, time, os
from pathlib import Path
def read_request():
    data = sys.stdin.buffer.read(4104)
    if len(data) != 4104:
        raise SystemExit(0)
    return struct.unpack('<Q', data[:8])[0]
def envelope(body=b'{"ok":true}'):
    return struct.pack('<I', len(body)) + body + bytes(65532-len(body))
def response(seq, status=0, data=None):
    return b'ETX1' + struct.pack('<QI', seq, status) + (envelope() if data is None else data)
def send(data):
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()
"#;

struct Fixture {
    backend: Arc<EteXProcessBackend>,
    directory: PathBuf,
}
impl Fixture {
    fn new(body: &str, timeout: Duration) -> Self {
        let directory = std::env::temp_dir().join(format!(
            "etex-supervisor-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&directory).unwrap();
        let script = directory.join("fixture.py");
        fs::write(&script, format!("{SUPPORT}\n{body}\n")).unwrap();
        let python = std::env::var_os("ETEX_TEST_PYTHON")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("/usr/bin/python3"));
        let mut config = EteXConfig::new(python, script, EteXEngine::Fixture);
        config.timeout = timeout;
        Self {
            backend: Arc::new(EteXProcessBackend::new(config).unwrap()),
            directory,
        }
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        self.backend.worker.lock().unwrap().take();
        fs::remove_dir_all(&self.directory).unwrap();
    }
}
fn input() -> Vec<u8> {
    let mut bytes = vec![0; INPUT_BYTES];
    bytes[..4].copy_from_slice(&2u32.to_le_bytes());
    bytes[4..6].copy_from_slice(b"{}");
    bytes
}
fn uncancelled() -> AtomicBool {
    AtomicBool::new(false)
}

#[test]
fn fixed_shapes_and_private_model_identity() {
    let f = Fixture::new("raise SystemExit(0)", Duration::from_secs(2));
    let a = TensorDesc {
        offset: 0,
        shape: vec![4096],
    };
    let b = TensorDesc {
        offset: 0,
        shape: vec![65536],
    };
    assert!(f.backend.supports(CAPABILITY));
    assert!(!f.backend.supports("vision.mnist.v1"));
    assert!(!f.backend.supports("/tmp/client.onnx"));
    assert_eq!(f.backend.validate(&a, &b, 0), Ok(()));
    assert_eq!(f.backend.validate(&a, &b, 1), Err(Error::Unsupported));
    assert_eq!(f.backend.validate(&a, &a, 0), Err(Error::Invalid));
    assert_eq!(
        f.backend.validate(
            &TensorDesc {
                offset: 0,
                shape: vec![64, 64]
            },
            &b,
            0
        ),
        Err(Error::Invalid)
    );
}

#[test]
fn invalid_envelopes_are_rejected_before_starting_a_worker() {
    let f = Fixture::new("raise SystemExit(0)", Duration::from_secs(2));
    let mut bad_padding = input();
    bad_padding[100] = 1;
    let mut oversized = input();
    oversized[..4].copy_from_slice(&4093u32.to_le_bytes());
    let mut invalid_utf8 = input();
    invalid_utf8[4] = 255;
    for bytes in [vec![], vec![0; 4096], bad_padding, oversized, invalid_utf8] {
        assert_eq!(
            f.backend.execute(&bytes, 0, &uncancelled()),
            Err(Error::Invalid)
        );
        assert!(f.backend.worker.lock().unwrap().is_none());
    }
    assert_eq!(
        f.backend.execute(&input(), 0, &AtomicBool::new(true)),
        Err(Error::Cancelled)
    );
}

#[test]
fn partial_response_reads_and_successive_sequences_reuse_one_worker() {
    let f = Fixture::new(
        r#"
send(b'ETEW1')
while True:
    seq = read_request()
    data = response(seq, data=envelope(('{"sequence":%d}' % seq).encode()))
    for offset in range(0, len(data), 113):
        send(data[offset:offset+113])
"#,
        Duration::from_secs(4),
    );
    let first = f.backend.execute(&input(), 0, &uncancelled()).unwrap();
    let pid = f
        .backend
        .worker
        .lock()
        .unwrap()
        .as_ref()
        .unwrap()
        .child
        .id();
    let second = f.backend.execute(&input(), 0, &uncancelled()).unwrap();
    assert_eq!(first.len(), OUTPUT_BYTES);
    assert_eq!(second.len(), OUTPUT_BYTES);
    assert_eq!(&first[4..18], b"{\"sequence\":1}");
    assert_eq!(&second[4..18], b"{\"sequence\":2}");
    assert_eq!(
        f.backend
            .worker
            .lock()
            .unwrap()
            .as_ref()
            .unwrap()
            .child
            .id(),
        pid
    );
}

#[test]
fn malformed_ready_headers_sequence_status_and_output_are_not_accepted() {
    let bodies = [
        "send(b'WRONG')",
        "send(b'ETEW1'); seq=read_request(); send(b'BAD!' + response(seq)[4:])",
        "send(b'ETEW1'); seq=read_request(); send(response(seq+1))",
        "send(b'ETEW1'); seq=read_request(); send(response(seq, 12345))",
        "send(b'ETEW1'); seq=read_request(); send(response(seq, data=bytes(65536)))",
        "send(b'ETEW1'); seq=read_request(); send(response(seq, data=struct.pack('<I',65533)+bytes(65532)))",
        "send(b'ETEW1'); seq=read_request(); send(response(seq, data=struct.pack('<I',1)+b'\\xff'+bytes(65531)))",
        "send(b'ETEW1'); seq=read_request(); send(response(seq, data=envelope()[:-1]+b'X'))",
        "send(b'ETEW1'); seq=read_request(); send(response(seq)[:100])",
    ];
    for body in bodies {
        let f = Fixture::new(body, Duration::from_secs(3));
        assert_eq!(
            f.backend.execute(&input(), 0, &uncancelled()),
            Err(Error::Backend),
            "{body}"
        );
        assert!(f.backend.worker.lock().unwrap().is_none());
    }
}

#[test]
fn worker_errors_propagate_without_committing_payload_or_replaying_job() {
    for code in [1, 2, 8, 9, 10] {
        let f = Fixture::new(
            &format!("send(b'ETEW1'); seq=read_request(); send(response(seq,{code}))"),
            Duration::from_secs(3),
        );
        assert_eq!(
            f.backend.execute(&input(), 0, &uncancelled()),
            Err(Error::from_code(code).unwrap())
        );
        assert!(f.backend.worker.lock().unwrap().is_none());
    }
    let f = Fixture::new(
        r#"
path=Path(__file__).with_name('starts')
count=int(path.read_text()) if path.exists() else 0
path.write_text(str(count+1))
send(b'ETEW1')
seq=read_request()
if count == 0: raise SystemExit(1)
send(response(seq))
time.sleep(5)
"#,
        Duration::from_secs(3),
    );
    assert_eq!(
        f.backend.execute(&input(), 0, &uncancelled()),
        Err(Error::Backend)
    );
    assert_eq!(fs::read_to_string(f.directory.join("starts")).unwrap(), "1");
    assert!(f.backend.execute(&input(), 0, &uncancelled()).is_ok());
    assert_eq!(fs::read_to_string(f.directory.join("starts")).unwrap(), "2");
}

#[test]
fn startup_and_response_deadlines_reap_direct_child() {
    unsafe extern "C" {
        fn kill(pid: i32, signal: i32) -> i32;
    }
    for prefix in ["", "send(b'ETEW1')"] {
        // Allow interpreter startup in a shared, CPU-quota constrained runner.
        // The fixture still cannot complete within the supervisor deadline.
        let f = Fixture::new(&format!("Path(__file__).with_name('pid').write_text(str(os.getpid()))\n{prefix}\ntime.sleep(10)"), Duration::from_secs(2));
        assert_eq!(
            f.backend.execute(&input(), 0, &uncancelled()),
            Err(Error::Timeout)
        );
        assert!(f.backend.worker.lock().unwrap().is_none());
        let pid: i32 = fs::read_to_string(f.directory.join("pid"))
            .unwrap()
            .parse()
            .unwrap();
        assert_eq!(unsafe { kill(pid, 0) }, -1);
        assert_eq!(io::Error::last_os_error().raw_os_error(), Some(3)); // ESRCH, not EPERM
    }
}

#[test]
fn cancellation_interrupts_waiting_for_worker_reply() {
    let f = Fixture::new(
        "send(b'ETEW1'); read_request(); time.sleep(5)",
        Duration::from_secs(4),
    );
    let flag = Arc::new(AtomicBool::new(false));
    let backend = f.backend.clone();
    let cloned = flag.clone();
    let task = thread::spawn(move || backend.execute(&input(), 0, &cloned));
    thread::sleep(Duration::from_millis(100));
    flag.store(true, Ordering::Release);
    assert_eq!(task.join().unwrap(), Err(Error::Cancelled));
    assert!(f.backend.worker.lock().unwrap().is_none());
}

#[test]
fn nonblocking_write_backpressure_obeys_deadline_and_cancellation() {
    // Intentionally fill the pipe beyond its capacity. A blocking write_all
    // would ignore cancellation/deadlines until the fixture exits five seconds later.
    for cancel in [false, true] {
        let f = Fixture::new("send(b'ETEW1'); time.sleep(5)", Duration::from_secs(3));
        let mut worker = f
            .backend
            .spawn(&uncancelled(), Instant::now() + Duration::from_secs(2))
            .unwrap();
        let flag = Arc::new(AtomicBool::new(false));
        let signal = flag.clone();
        let trigger = if cancel {
            Some(thread::spawn(move || {
                thread::sleep(Duration::from_millis(50));
                signal.store(true, Ordering::Release);
            }))
        } else {
            None
        };
        let started = Instant::now();
        let result = worker.write_frame(
            &vec![0; 2 * 1024 * 1024],
            &flag,
            started + Duration::from_millis(200),
        );
        assert_eq!(
            result,
            Err(if cancel {
                Error::Cancelled
            } else {
                Error::Timeout
            })
        );
        assert!(started.elapsed() < Duration::from_secs(3));
        if let Some(trigger) = trigger {
            trigger.join().unwrap();
        }
    }
}

#[test]
fn waiting_for_backend_lock_is_cancellable() {
    let f = Fixture::new("raise SystemExit(0)", Duration::from_secs(2));
    let _guard = f.backend.worker.lock().unwrap();
    let flag = Arc::new(AtomicBool::new(false));
    let backend = f.backend.clone();
    let cloned = flag.clone();
    let task = thread::spawn(move || backend.execute(&input(), 0, &cloned));
    thread::sleep(Duration::from_millis(50));
    flag.store(true, Ordering::Release);
    assert_eq!(task.join().unwrap(), Err(Error::Cancelled));
}
