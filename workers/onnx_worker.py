# SPDX-License-Identifier: Apache-2.0
"""Fixed, local MNIST capability. Trusted interpreter/script configuration only."""
import ctypes
import errno
import hashlib
import os
import resource
import socket
import sys

MODEL_SHA256 = "2f06e72de813a8635c9bc0397ac447a601bdbfa7df4bebc278723b958831c9bf"
MODEL_SIZE = 26454


def restrict_worker():
    """Fail closed if syscall restrictions cannot be installed, before model parsing."""
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
        raise RuntimeError("no_new_privs unavailable")
    seccomp = ctypes.CDLL("libseccomp.so.2", use_errno=True)
    seccomp.seccomp_init.argtypes = [ctypes.c_uint32]
    seccomp.seccomp_init.restype = ctypes.c_void_p
    seccomp.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    seccomp.seccomp_syscall_resolve_name.restype = ctypes.c_int
    seccomp.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    seccomp.seccomp_rule_add.restype = ctypes.c_int
    seccomp.seccomp_load.argtypes = [ctypes.c_void_p]
    seccomp.seccomp_load.restype = ctypes.c_int
    seccomp.seccomp_release.argtypes = [ctypes.c_void_p]
    ctx = seccomp.seccomp_init(0x7FFF0000)  # SCMP_ACT_ALLOW; explicit denied surfaces below.
    if not ctx:
        raise RuntimeError("seccomp allocation failed")
    denied = """
        open openat openat2 creat open_by_handle_at name_to_handle_at
        socket socketpair connect bind listen accept accept4 sendto recvfrom sendmsg recvmsg
        execve execveat fork vfork clone clone3 ptrace process_vm_readv process_vm_writev
        pidfd_open pidfd_getfd pidfd_send_signal kill tkill tgkill
        io_uring_setup bpf userfaultfd mount umount2 pivot_root chroot unshare setns
        fsopen fsconfig fsmount fspick open_tree move_mount mount_setattr
        unlink unlinkat rename renameat renameat2 mkdir mkdirat rmdir link linkat symlink symlinkat
        chmod fchmod fchmodat chown fchown lchown fchownat truncate
        mknod mknodat setxattr lsetxattr fsetxattr removexattr lremovexattr fremovexattr
    """.split()
    try:
        for name in denied:
            number = seccomp.seccomp_syscall_resolve_name(name.encode("ascii"))
            if number >= 0 and seccomp.seccomp_rule_add(ctx, 0x00050000 | errno.EPERM, number, 0) != 0:
                raise RuntimeError("seccomp rule failed")
        if seccomp.seccomp_load(ctx) != 0:
            raise RuntimeError("seccomp installation failed")
    finally:
        seccomp.seccomp_release(ctx)
    # Positive checks prevent a worker from announcing readiness without restrictions.
    for probe in (lambda: os.open("/etc/passwd", os.O_RDONLY), lambda: socket.socket(), os.fork):
        try:
            probe()
        except OSError as error:
            if error.errno != errno.EPERM:
                raise
        else:
            raise RuntimeError("worker restriction probe unexpectedly succeeded")


def main():
    if sys.platform != "linux" or len(sys.argv) != 3 or os.geteuid() == 0:
        raise RuntimeError("worker requires non-root Linux and configured model/parent")
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, 9, 0, 0, 0) != 0 or os.getppid() != int(sys.argv[2]):  # PDEATHSIG SIGKILL
        raise RuntimeError("worker parent is unavailable")
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
    with open(sys.argv[1], "rb") as model_file:
        model = model_file.read(MODEL_SIZE + 1)
    if len(model) != MODEL_SIZE or hashlib.sha256(model).hexdigest() != MODEL_SHA256:
        raise RuntimeError("unapproved model artifact")
    import numpy as np
    import onnxruntime as ort
    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.log_severity_level = 3
    options.enable_mem_pattern = False
    # Libraries and approved bytes are in memory; model parsing is still downstream.
    os.closerange(3, 64)
    restrict_worker()
    session = ort.InferenceSession(model, sess_options=options, providers=["CPUExecutionProvider"])
    inputs, outputs = session.get_inputs(), session.get_outputs()
    if (len(inputs) != 1 or inputs[0].shape != [1, 1, 28, 28] or inputs[0].type != "tensor(float)"
            or len(outputs) != 1 or outputs[0].shape != [1, 10]):
        raise RuntimeError("unexpected model contract")
    sys.stdout.buffer.write(b"COGW1")
    sys.stdout.buffer.flush()
    last_id = 0
    while True:
        data = sys.stdin.buffer.read(792)
        if not data:
            return
        if len(data) != 792:
            raise RuntimeError("truncated worker request")
        request_id = int.from_bytes(data[:8], "little")
        if request_id != last_id + 1:
            raise RuntimeError("invalid worker request sequence")
        last_id = request_id
        image = np.frombuffer(data[8:], dtype=np.uint8).astype(np.float32).reshape(1, 1, 28, 28) / np.float32(255)
        logits = session.run(None, {inputs[0].name: image})[0]
        if logits.shape != (1, 10) or not np.isfinite(logits).all():
            raise RuntimeError("invalid inference output")
        sys.stdout.buffer.write(b"DIG1" + data[:8] + bytes([int(np.argmax(logits[0]))]))
        sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
