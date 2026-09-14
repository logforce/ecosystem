/* SPDX-License-Identifier: Apache-2.0 */
#include "cogposix/cog.h"
#include <stdio.h>
#include <string.h>

#define CHECK(call) do { cog_status_t s = (call); if (s != COG_OK) { \
    fprintf(stderr, "%s failed: %d\n", #call, s); if (ctx) cog_context_destroy(ctx); return 1; } } while (0)

int main(int argc, char **argv) {
    if (argc != 2) { fprintf(stderr, "usage: mock-client SOCKET\n"); return 2; }
    cog_context_t ctx = 0;
    cog_handle_t model, input, output, a, b, job;
    const uint8_t data[] = {0, 1, 254, 255};
    const uint8_t expected[] = {1, 2, 255, 0};
    uint8_t result[4] = {0}; uint64_t written = 0;
    cog_tensor_desc_t desc = {0};
    desc.struct_size = sizeof(desc); desc.dtype = COG_DTYPE_U8; desc.rank = 1; desc.shape[0] = 4;
    CHECK(cog_context_create(argv[1], &ctx));
    CHECK(cog_model_open(ctx, "mock.increment.v1", &model));
    CHECK(cog_buffer_alloc(ctx, 4, &input)); CHECK(cog_buffer_alloc(ctx, 4, &output));
    CHECK(cog_buffer_write(ctx, input, data, 4));
    CHECK(cog_tensor_create(ctx, input, &desc, &a)); CHECK(cog_tensor_create(ctx, output, &desc, &b));
    CHECK(cog_infer_submit(ctx, model, a, b, 0, &job));
    CHECK(cog_job_wait(ctx, job, 5000)); CHECK(cog_buffer_read(ctx, output, result, 4, &written));
    if (written != 4 || memcmp(result, expected, 4) != 0) {
        fprintf(stderr, "unexpected result\n"); cog_context_destroy(ctx); return 1;
    }
    CHECK(cog_job_release(ctx, job)); CHECK(cog_tensor_release(ctx, a)); CHECK(cog_tensor_release(ctx, b));
    CHECK(cog_buffer_free(ctx, input)); CHECK(cog_buffer_free(ctx, output)); CHECK(cog_model_close(ctx, model));
    CHECK(cog_context_destroy(ctx));
    puts("C ABI verified: [0,1,254,255] -> [1,2,255,0]"); return 0;
}
