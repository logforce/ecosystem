/* SPDX-License-Identifier: Apache-2.0 */
#define _GNU_SOURCE
#include "cogposix/cog.h"
#include <assert.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

int main(int argc, char **argv) {
    assert(argc == 2);
    cog_context_t ctx;
    assert(cog_context_create(argv[1], &ctx) == COG_OK);
    uint64_t features;
    assert(cog_context_features(ctx, &features) == COG_OK);
    assert(features & COG_FEATURE_SEALED_SHM);
    int fd = memfd_create("c-shm-test", MFD_CLOEXEC | MFD_ALLOW_SEALING);
    assert(fd >= 0 && write(fd, "abcd", 4) == 4);
    cog_handle_t input, output, model, a, b, job;
    assert(cog_buffer_import_fd(ctx, -1, 4, &input) == COG_EINVAL);
    assert(cog_buffer_import_fd(ctx, fd, 4, &input) == COG_EPERM);
    void *writable = mmap(NULL, 4, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    assert(writable != MAP_FAILED);
    int seals = F_SEAL_WRITE | F_SEAL_GROW | F_SEAL_SHRINK | F_SEAL_SEAL;
    assert(fcntl(fd, F_ADD_SEALS, seals) == -1);
    assert(munmap(writable, 4) == 0);
    assert(fcntl(fd, F_ADD_SEALS, seals) == 0);
    assert(pwrite(fd, "x", 1, 0) == -1);
    assert(ftruncate(fd, 1) == -1 && ftruncate(fd, 8) == -1);
    assert(mmap(NULL, 4, PROT_WRITE, MAP_SHARED, fd, 0) == MAP_FAILED);
    assert(cog_buffer_import_fd(ctx, fd, 3, &input) == COG_EINVAL);
    assert(cog_buffer_import_fd(ctx, fd, 4, &input) == COG_OK);
    close(fd);
    assert(cog_buffer_alloc(ctx, 4, &output) == COG_OK);
    assert(cog_model_open(ctx, "mock.increment.v1", &model) == COG_OK);
    cog_tensor_desc_t desc = { .struct_size = sizeof(desc), .dtype = COG_DTYPE_U8, .rank = 1, .shape = {4} };
    assert(cog_tensor_create(ctx, input, &desc, &a) == COG_OK);
    assert(cog_tensor_create(ctx, output, &desc, &b) == COG_OK);
    assert(cog_infer_submit(ctx, model, a, b, 0, &job) == COG_OK);
    assert(cog_job_wait(ctx, job, 5000) == COG_OK);
    int32_t snapshot;
    uint64_t size;
    assert(cog_buffer_export_fd(ctx, output, &snapshot, &size) == COG_OK && size == 4);
    assert(fcntl(snapshot, F_GETFD) & FD_CLOEXEC);
    assert((fcntl(snapshot, F_GET_SEALS) & seals) == seals);
    const void *mapped = mmap(NULL, size, PROT_READ, MAP_PRIVATE, snapshot, 0);
    assert(mapped != MAP_FAILED && memcmp(mapped, "bcde", 4) == 0);
    assert(cog_context_destroy(ctx) == COG_OK);
    assert(memcmp(mapped, "bcde", 4) == 0);
    munmap((void *)mapped, size);
    close(snapshot);
    puts("C shared memory: seals, immutable mappings, descriptor ownership and inference verified.");
    return 0;
}
