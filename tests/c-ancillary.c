/* SPDX-License-Identifier: Apache-2.0 */
#define _GNU_SOURCE
#include <assert.h>
#include <dirent.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <unistd.h>

extern ssize_t cog_shm_recv(int, unsigned char *, size_t, int *);
static int descriptor_count(void) {
    DIR *d = opendir("/proc/self/fd");
    assert(d);
    int n = 0;
    struct dirent *entry;
    while ((entry = readdir(d))) if (entry->d_name[0] != '.') n++;
    closedir(d);
    return n;
}
int main(void) {
    int pair[2];
    assert(socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, pair) == 0);
    int fd = memfd_create("ancillary-test", MFD_CLOEXEC);
    assert(fd >= 0);
    int baseline = descriptor_count();
    for (int iteration = 0; iteration < 100; iteration++) {
        /* Two descriptors exercise extra-FD rejection; sixteen force truncation. */
        for (int count = 1; count <= 16; count *= 2) {
            unsigned char byte = 'C';
            struct iovec iov = { .iov_base = &byte, .iov_len = 1 };
            union { struct cmsghdr align; unsigned char bytes[CMSG_SPACE(16 * sizeof(int))]; } control = {0};
            struct msghdr msg = { .msg_iov = &iov, .msg_iovlen = 1,
                .msg_control = control.bytes, .msg_controllen = CMSG_SPACE(count * sizeof(int)) };
            struct cmsghdr *c = CMSG_FIRSTHDR(&msg);
            c->cmsg_level = SOL_SOCKET; c->cmsg_type = SCM_RIGHTS;
            c->cmsg_len = CMSG_LEN(count * sizeof(int));
            for (int i = 0; i < count; i++) memcpy(CMSG_DATA(c) + i * sizeof(int), &fd, sizeof(fd));
            assert(sendmsg(pair[0], &msg, MSG_NOSIGNAL) == 1);
            int received = -1;
            ssize_t result = cog_shm_recv(pair[1], &byte, 1, &received);
            if (count == 1) {
                assert(result == 1 && received >= 0);
                assert(fcntl(received, F_GETFD) & FD_CLOEXEC);
                close(received);
            } else assert(result == -1 && received == -1);
            assert(descriptor_count() == baseline);
        }
    }
    close(fd); close(pair[0]); close(pair[1]);
    puts("Ancillary transport: 500 transfers, excess/truncated descriptors rejected, no FD leaks.");
    return 0;
}
