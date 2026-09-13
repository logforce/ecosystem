/* SPDX-License-Identifier: Apache-2.0 */
#ifndef COGPOSIX_COG_H
#define COGPOSIX_COG_H
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif

/* Experimental ABI 0.1. Linux/macOS 64-bit, native C alignment.
 * All handles require their originating context. No raw pointer crosses IPC.
 * All functions return COG_OK or a positive error code. Output handles are zeroed
 * on failure when a valid output pointer is supplied. Pointers must be aligned,
 * valid for the declared storage, and remain valid through the call. Strings
 * must be NUL-terminated UTF-8. Output parameters must not alias input storage.
 * Calls on a context are serialized; wait releases the lock between polls so a
 * different thread may cancel. Finish all calls before destroying their context.
 * Do not use this library in a child after fork; exec before opening new contexts.
 */
#define COG_ABI_MAJOR 0
#define COG_ABI_MINOR 1
#define COG_MAX_RANK 8
#define COG_DTYPE_U8 1
typedef uint64_t cog_context_t;
typedef uint64_t cog_handle_t;
typedef int32_t cog_status_t;
enum {
    COG_OK = 0, COG_EINVAL = 1, COG_ENOMEM = 2, COG_ENOMODEL = 3,
    COG_EBUSY = 4, COG_ETIMEOUT = 5, COG_ECANCELLED = 6, COG_EPERM = 7,
    COG_EBACKEND = 8, COG_EIO = 9, COG_EUNSUPPORTED = 10, COG_EDISCONNECTED = 11
};
enum { COG_QUEUED = 1, COG_RUNNING = 2, COG_COMPLETED = 3, COG_FAILED = 4, COG_CANCELLED = 5 };
typedef struct cog_tensor_desc {
    uint32_t struct_size; /* sizeof(cog_tensor_desc_t) */
    uint32_t dtype;       /* COG_DTYPE_U8 only in R1 */
    uint32_t rank;
    uint32_t flags;       /* zero */
    uint64_t offset;
    uint64_t shape[COG_MAX_RANK]; /* unused entries zero; contiguous layout */
} cog_tensor_desc_t;

cog_status_t cog_context_create(const char *socket, cog_context_t *out);
cog_status_t cog_context_destroy(cog_context_t context);
cog_status_t cog_model_open(cog_context_t context, const char *name, cog_handle_t *out);
cog_status_t cog_model_close(cog_context_t context, cog_handle_t model);
cog_status_t cog_buffer_alloc(cog_context_t context, uint64_t size, cog_handle_t *out);
/* R1 mock transport copies whole buffers; it is not shared memory or zero-copy. */
cog_status_t cog_buffer_write(cog_context_t context, cog_handle_t buffer, const uint8_t *data, uint64_t size);
/* On insufficient capacity returns ENOMEM and the required size in written. */
cog_status_t cog_buffer_read(cog_context_t context, cog_handle_t buffer, uint8_t *data, uint64_t capacity, uint64_t *written);
cog_status_t cog_buffer_free(cog_context_t context, cog_handle_t buffer);
cog_status_t cog_tensor_create(cog_context_t context, cog_handle_t buffer, const cog_tensor_desc_t *desc, cog_handle_t *out);
cog_status_t cog_tensor_release(cog_context_t context, cog_handle_t tensor);
/* One input/output, equal shape, different buffers. Delay 0..5000ms is mock-only. */
cog_status_t cog_infer_submit(cog_context_t context, cog_handle_t model, cog_handle_t input, cog_handle_t output, uint32_t delay_ms, cog_handle_t *out_job);
cog_status_t cog_job_status(cog_context_t context, cog_handle_t job, uint32_t *state, int32_t *execution_error);
/* Milliseconds, monotonic polling. Timeout does not cancel. Each IPC can take up
 * to 5 seconds on transport failure; this is not a hard deadline API. */
cog_status_t cog_job_wait(cog_context_t context, cog_handle_t job, uint64_t timeout_ms);
/* Queued cancellation is terminal; running cancellation completes asynchronously. */
cog_status_t cog_job_cancel(cog_context_t context, cog_handle_t job);
/* Release returns EBUSY until terminal. Other resources may be closed while pinned. */
cog_status_t cog_job_release(cog_context_t context, cog_handle_t job);

#ifdef __cplusplus
}
#endif
#endif
