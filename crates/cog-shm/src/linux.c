/* SPDX-License-Identifier: Apache-2.0 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stddef.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/vfs.h>
#include <unistd.h>

int cog_shm_create(void) { return memfd_create("cogposix", MFD_CLOEXEC | MFD_ALLOW_SEALING); }
int cog_shm_seal(int fd) {
    return fcntl(fd, F_ADD_SEALS, F_SEAL_WRITE | F_SEAL_GROW | F_SEAL_SHRINK | F_SEAL_SEAL);
}
int cog_shm_validate(int fd) {
    struct statfs fs;
    if (fstatfs(fd, &fs) != 0 || fs.f_type != 0x01021994) { errno = EINVAL; return -1; }
    int seals = fcntl(fd, F_GET_SEALS);
    int required = F_SEAL_WRITE | F_SEAL_GROW | F_SEAL_SHRINK | F_SEAL_SEAL;
    if (seals < 0 || (seals & required) != required) { errno = EINVAL; return -1; }
    return 0;
}
int cog_shm_duplicate(int fd) { return fcntl(fd, F_DUPFD_CLOEXEC, 0); }
int cog_shm_nonblocking(int fd) {
    int flags = fcntl(fd, F_GETFL);
    return flags < 0 ? -1 : fcntl(fd, F_SETFL, flags | O_NONBLOCK);
}
void *cog_shm_map(int fd, size_t len) {
    /* Old kernels reject MAP_SHARED on write-sealed O_RDWR memfds. A read-only
     * private mapping still reads the immutable file pages without a data copy. */
    void *p = mmap(NULL, len, PROT_READ, MAP_PRIVATE, fd, 0);
    return p == MAP_FAILED ? NULL : p;
}
void cog_shm_unmap(void *p, size_t len) { munmap(p, len); }

/* Ancillary data accompanies exactly the first byte of a frame. */
ssize_t cog_shm_send(int socket, const unsigned char *byte, int fd) {
    struct iovec iov = { .iov_base = (void *)byte, .iov_len = 1 };
    union { struct cmsghdr align; unsigned char bytes[CMSG_SPACE(sizeof(int))]; } control = {0};
    struct msghdr msg = { .msg_iov = &iov, .msg_iovlen = 1 };
    if (fd >= 0) {
        msg.msg_control = control.bytes;
        msg.msg_controllen = sizeof(control.bytes);
        struct cmsghdr *c = CMSG_FIRSTHDR(&msg);
        c->cmsg_level = SOL_SOCKET;
        c->cmsg_type = SCM_RIGHTS;
        c->cmsg_len = CMSG_LEN(sizeof(int));
        memcpy(CMSG_DATA(c), &fd, sizeof(fd));
    }
    ssize_t n;
    do { n = sendmsg(socket, &msg, MSG_NOSIGNAL); } while (n < 0 && errno == EINTR);
    return n;
}

ssize_t cog_shm_recv(int socket, unsigned char *bytes, size_t len, int *fd) {
    struct iovec iov = { .iov_base = bytes, .iov_len = len };
    union { struct cmsghdr align; unsigned char bytes[CMSG_SPACE(8 * sizeof(int))]; } control = {0};
    struct msghdr msg = { .msg_iov = &iov, .msg_iovlen = 1,
        .msg_control = control.bytes, .msg_controllen = sizeof(control.bytes) };
    *fd = -1;
    ssize_t n;
    do { n = recvmsg(socket, &msg, MSG_CMSG_CLOEXEC); } while (n < 0 && errno == EINTR);
    if (n < 0) return n;
    int count = 0, bad = (msg.msg_flags & (MSG_CTRUNC | MSG_TRUNC)) != 0;
    for (struct cmsghdr *c = CMSG_FIRSTHDR(&msg); c; c = CMSG_NXTHDR(&msg, c)) {
        if (c->cmsg_level != SOL_SOCKET || c->cmsg_type != SCM_RIGHTS || c->cmsg_len < CMSG_LEN(0)) {
            bad = 1; continue;
        }
        size_t size = c->cmsg_len - CMSG_LEN(0);
        if (size % sizeof(int)) bad = 1;
        for (size_t i = 0; i + sizeof(int) <= size; i += sizeof(int)) {
            int received;
            memcpy(&received, CMSG_DATA(c) + i, sizeof(received));
            if (count++ == 0) *fd = received;
            else { close(received); bad = 1; }
        }
    }
    if (bad) {
        if (*fd >= 0) close(*fd);
        *fd = -1; errno = EPROTO; return -1;
    }
    return n;
}
