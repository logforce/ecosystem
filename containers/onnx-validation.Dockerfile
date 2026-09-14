# SPDX-License-Identifier: Apache-2.0
# Local development image, not a redistributable ecOS release.
FROM ecos-cogposix-validation:rust-1.90.0-node-22.19.0
USER root
RUN apt-get update && apt-get install -y --no-install-recommends python3-venv libseccomp2 && rm -rf /var/lib/apt/lists/*
COPY workers/requirements.txt /opt/ecos-requirements.txt
RUN python3 -m venv /opt/ecos-onnx && /opt/ecos-onnx/bin/pip install --no-cache-dir -r /opt/ecos-requirements.txt
COPY scripts/fetch-mnist.mjs /opt/fetch-mnist.mjs
RUN node /opt/fetch-mnist.mjs /opt/ecos-models
USER 10001:10001
