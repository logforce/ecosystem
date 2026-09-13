/* SPDX-License-Identifier: Apache-2.0 */
#define _POSIX_C_SOURCE 200809L
#include "cogposix/cog.h"
#include <assert.h>
#include <pthread.h>
#include <stdio.h>
#include <time.h>

_Static_assert(sizeof(cog_tensor_desc_t) == 88, "unexpected tensor ABI layout");
struct wait_args { cog_context_t ctx; cog_handle_t job; cog_status_t result; };
static void *wait_job(void *data) {
    struct wait_args *args = data;
    args->result = cog_job_wait(args->ctx, args->job, 5000);
    return NULL;
}

int main(int argc, char **argv) {
    assert(argc == 2);
    cog_context_t a = 0, b = 0;
    cog_handle_t model = 0, input = 0, output = 0, ta = 0, tb = 0, job = 0;
    uint8_t data[4] = {1,2,3,255}; uint64_t written = 0;
    assert(cog_context_create(NULL, &a) == COG_EINVAL);
    assert(cog_context_create(argv[1], &a) == COG_OK);
    assert(cog_context_create(argv[1], &b) == COG_OK);
    assert(cog_model_open(a, "mock.increment.v1", &model) == COG_OK);
    assert(cog_buffer_alloc(a, 4, &input) == COG_OK);
    assert(cog_buffer_alloc(a, 4, &output) == COG_OK);
    assert(cog_buffer_write(a, input, data, 4) == COG_OK);
    assert(cog_buffer_read(b, input, data, 4, &written) == COG_EINVAL);
    assert(cog_buffer_read(a, input, NULL, 0, &written) == COG_ENOMEM && written == 4);
    assert(cog_buffer_write(a, input, NULL, 4) == COG_EINVAL);
    cog_tensor_desc_t desc = {0};
    desc.struct_size = sizeof(desc); desc.dtype = COG_DTYPE_U8; desc.rank = 1; desc.shape[0] = 4;
    desc.flags = 1;
    assert(cog_tensor_create(a, input, &desc, &ta) == COG_EUNSUPPORTED && ta == 0);
    desc.flags = 0;
    assert(cog_tensor_create(a, input, &desc, &ta) == COG_OK);
    assert(cog_tensor_create(a, output, &desc, &tb) == COG_OK);
    assert(cog_infer_submit(a, model, ta, tb, 5000, &job) == COG_OK);
    assert(cog_job_wait(a, job, 0) == COG_ETIMEOUT);
    struct wait_args args = {a, job, COG_OK}; pthread_t thread;
    assert(pthread_create(&thread, NULL, wait_job, &args) == 0);
    struct timespec pause = {0, 20000000}; nanosleep(&pause, NULL);
    assert(cog_job_cancel(a, job) == COG_OK);
    assert(pthread_join(thread, NULL) == 0);
    assert(args.result == COG_ECANCELLED);
    assert(cog_job_release(a, job) == COG_OK);
    assert(cog_job_release(a, job) == COG_EINVAL);
    assert(cog_context_destroy(a) == COG_OK);
    assert(cog_context_destroy(a) == COG_EINVAL);
    assert(cog_context_destroy(b) == COG_OK);
    puts("C ABI errors, ownership, layout, size query and concurrent cancellation verified.");
    return 0;
}
