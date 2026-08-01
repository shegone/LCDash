FROM ghcr.io/open-webui/computer@sha256:bbfb7e6716c5adb024a75c0e3fbbdd7c4334cc0fc47071c83fbc0b58e9a29e2d

USER root

ADD --checksum=sha256:a4dffcc00a5a93256c6bd06aa0c984320528f564db52a1f4becd5c7de9fb59a1 \
    https://github.com/anomalyco/opencode/releases/download/v1.18.11/opencode-linux-x64.tar.gz \
    /tmp/opencode.tar.gz

RUN tar -xzf /tmp/opencode.tar.gz -C /usr/local/bin \
    && chmod 0755 /usr/local/bin/opencode \
    && rm -f /tmp/opencode.tar.gz \
    && opencode --version

USER cptr
