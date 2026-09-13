# SPDX-License-Identifier: Apache-2.0
# Development tooling only. No project source or private working tree is copied.
FROM node:22.19.0-bookworm-slim@sha256:4a4884e8a44826194dff92ba316264f392056cbe243dcc9fd3551e71cea02b90 AS node
FROM rust:1.90.0-bookworm@sha256:3914072ca0c3b8aad871db9169a651ccfce30cf58303e5d6f2db16d1d8a7e58f
COPY --from=node /usr/local/bin/node /usr/local/bin/node
RUN rustup component add rustfmt clippy
RUN mkdir -m 1777 /work
USER 10001:10001
WORKDIR /work
