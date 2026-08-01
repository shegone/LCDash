FROM ghcr.io/open-webui/computer@sha256:bbfb7e6716c5adb024a75c0e3fbbdd7c4334cc0fc47071c83fbc0b58e9a29e2d

USER root

ADD --checksum=sha256:9b09b4cd20beed42c3308267f1f663f1f79c76a7e6e0e9bdfc8d05536430628e \
    https://github.com/anomalyco/opencode/releases/download/v1.1.51/opencode-linux-x64.tar.gz \
    /tmp/opencode.tar.gz

RUN tar -xzf /tmp/opencode.tar.gz -C /usr/local/bin \
    && chmod 0755 /usr/local/bin/opencode \
    && rm -f /tmp/opencode.tar.gz \
    && opencode --version

USER cptr
