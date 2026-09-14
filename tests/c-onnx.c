/* SPDX-License-Identifier: Apache-2.0 */
#define _GNU_SOURCE
#include "cogposix/cog.h"
#include <assert.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <unistd.h>

int main(int argc, char **argv) {
    assert(argc == 4);
    unsigned char image[784];
    FILE *source = fopen(argv[2], "rb");
    assert(source && fread(image, 1, sizeof(image), source) == sizeof(image));
    fclose(source);
    int fd = memfd_create("onnx-c-test", MFD_CLOEXEC | MFD_ALLOW_SEALING);
    assert(fd >= 0 && write(fd, image, sizeof(image)) == sizeof(image));
    assert(fcntl(fd, F_ADD_SEALS, F_SEAL_WRITE | F_SEAL_GROW | F_SEAL_SHRINK | F_SEAL_SEAL) == 0);
    cog_context_t context;
    cog_handle_t model, input, output, a, b, job;
    assert(cog_context_create(argv[1], &context) == COG_OK);
    assert(cog_model_open(context, "vision.mnist.v1", &model) == COG_OK);
    assert(cog_buffer_import_fd(context, fd, sizeof(image), &input) == COG_OK);
    close(fd);
    assert(cog_buffer_alloc(context, 1, &output) == COG_OK);
    cog_tensor_desc_t in = { .struct_size = sizeof(in), .dtype = COG_DTYPE_U8, .rank = 2, .shape = {28, 28} };
    cog_tensor_desc_t out = { .struct_size = sizeof(out), .dtype = COG_DTYPE_U8, .rank = 1, .shape = {1} };
    assert(cog_tensor_create(context, input, &in, &a) == COG_OK);
    assert(cog_tensor_create(context, output, &out, &b) == COG_OK);
    assert(cog_infer_submit(context, model, a, b, 0, &job) == COG_OK);
    assert(cog_buffer_free(context, input) == COG_OK);
    assert(cog_tensor_release(context, a) == COG_OK);
    assert(cog_job_wait(context, job, 15000) == COG_OK);
    uint8_t digit; uint64_t written;
    assert(cog_buffer_read(context, output, &digit, 1, &written) == COG_OK);
    assert(written == 1 && digit == (unsigned)atoi(argv[3]));
    assert(cog_context_destroy(context) == COG_OK);
    puts("C ONNX classification and shared-input lifetime verified.");
    return 0;
}
