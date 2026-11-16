# syntax=docker/dockerfile:1.7
# K1 app image — HARDENED (6B-7), unified to canonical files/docker/k1/ 2026-11-08.
# K1-DEPS (2026-11-13): pinned scanner toolchain added at build time only.
# Owner/FDE build only. Build from repo root, no pull:
#   docker build --pull=false -t k1-app:latest files/docker/k1
#
# Pins (verified 2026-11-08 against local images, offline):
#   python:3.14-slim@sha256:0514bc76712d64f8bb48adcb95b1ab0d88c69fee95d62fe4c18ee948bee9b1e1
#   postgres:17-alpine@sha256:b0f9560a2de083e2cc7382e75f808c7381a32852a7ec49117deedb300e552b24
#     (sidecar in compose.yaml; full pins in image-pins.json)
#
# Tool pins (K1-DEPS, downloads at BUILD time only, every archive sha256-checked):
#   Python (requirements.lock): schemathesis 4.33.4, hypothesis 6.170.15,
#     zizmor 1.32.0, radon 6.0.1, bandit 1.9.4 (+ hashed transitive deps).
#   Eclipse Temurin JRE 21.0.13+9 (ZAP needs Java >= 17)
#     sha256:a535a1df9c6284d9618872984e026ae0352d1f7e9e0605425c0e6114f47351dd
#   ZAP 2.17.0 core (zap.sh + bundled add-ons, no JRE, no browser)
#     sha256:0cb73b7f72d12c263fb61de304edb82a455d7aa4e1813c216c061765c306f5b7
#   zap-baseline wrapper: files/docker/k1/zap-baseline (this build context)
#
# Network (F-13): no ports are published. The app's fake provider binds
# 127.0.0.1:8081 inside the container on an internal network, so there
# is no 0.0.0.0 publish. pg2a/pg2b are outside K1.
#
# /work: rw,exec,nosuid,nodev tmpfs (see compose.yaml). exec is required to run
# built artifacts per the K1 contract; compensating controls are internal-only
# network, read-only rootfs, cap-drop ALL, no-new-privileges, and non-root
# uid/gid 10001. /tmp, /run, and /home/k1 stay noexec.
#
# Leanness / no per-run installs: the build stage installs the pinned runtime
# dependencies from requirements.txt ONCE at image build. The runtime stage
# copies only the resulting tree. Neither stage of the running container runs
# pip, apt, npm (or any package manager), and the typed k1.up tool never
# install or build: k1.up refuses with IMAGE_MISSING when k1-app:latest is absent.
# Runtime writes are confined to tmpfs /tmp, /run, /work, /home/k1; the rootfs
# is read-only. The toolchain is read-only at /opt: ZAP's home is redirected to
# a tmpfs work dir by /usr/local/bin/zap-baseline.
#
# Multi-stage: the build stage only installs pinned runtime dependencies from
# requirements.txt and unpacks the pinned JRE/ZAP archives. No application code
# is baked in; the sealed repo is mounted read-only at /repo/base and /repo/head.

FROM python:3.14-slim@sha256:0514bc76712d64f8bb48adcb95b1ab0d88c69fee95d62fe4c18ee948bee9b1e1 AS build
ENV PIP_NO_CACHE_DIR=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /install
COPY requirements.txt requirements.lock ./
RUN mkdir -p /install \
    && pip install --no-cache-dir --prefix=/install -r requirements.txt

# --- pinned JRE + ZAP, fetched and verified at build time only -------------
ARG TEMURIN_URL=https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.13%2B9/OpenJDK21U-jre_x64_linux_hotspot_21.0.13_9.tar.gz
ARG TEMURIN_SHA256=a535a1df9c6284d9618872984e026ae0352d1f7e9e0605425c0e6114f47351dd
ARG ZAP_VERSION=2.17.0
ARG ZAP_SHA256=0cb73b7f72d12c263fb61de304edb82a455d7aa4e1813c216c061765c306f5b7
RUN set -eux; \
    python3 -c 'import sys,urllib.request; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])' \
        "${TEMURIN_URL}" /tmp/jre.tar.gz; \
    echo "${TEMURIN_SHA256}  /tmp/jre.tar.gz" | sha256sum -c -; \
    mkdir -p /opt/java; \
    tar -xzf /tmp/jre.tar.gz -C /opt/java --strip-components=1; \
    rm -f /tmp/jre.tar.gz; \
    python3 -c 'import sys,urllib.request; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])' \
        "https://github.com/zaproxy/zaproxy/releases/download/v${ZAP_VERSION}/ZAP_${ZAP_VERSION}_Core.zip" \
        /tmp/zap.zip; \
    echo "${ZAP_SHA256}  /tmp/zap.zip" | sha256sum -c -; \
    python3 -c 'import sys,zipfile; zipfile.ZipFile(sys.argv[1]).extractall("/opt")' /tmp/zap.zip; \
    mv "/opt/ZAP_${ZAP_VERSION}" /opt/zap; \
    rm -f /tmp/zap.zip; \
    chmod 0755 /opt/zap/zap.sh; \
    /opt/java/bin/java -version

FROM python:3.14-slim@sha256:0514bc76712d64f8bb48adcb95b1ab0d88c69fee95d62fe4c18ee948bee9b1e1
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/repo/head/backend \
    JAVA_HOME=/opt/java \
    ZAP_SH=/opt/zap/zap.sh \
    ZAP_WORK_DIR=/work/zap \
    PATH="/opt/java/bin:/opt/zap:/usr/local/bin:${PATH}"
RUN useradd --uid 10001 --create-home --shell /bin/sh k1 \
    && mkdir -p /work /repo/base /repo/head \
    && chown 10001:10001 /work
COPY --from=build /install /usr/local
COPY --from=build /opt/java /opt/java
COPY --from=build /opt/zap /opt/zap
COPY fake_provider.py seed.py entrypoint.sh /opt/k1/
COPY probe_client.py /opt/k1/probe_client.py
COPY zap-baseline /usr/local/bin/zap-baseline
# Stable names the gate resolves through PATH: `zap`, `zap.sh`, `zap-baseline`.
RUN ln -s /opt/zap/zap.sh /usr/local/bin/zap.sh \
    && ln -s /opt/zap/zap.sh /usr/local/bin/zap \
    && chmod 0555 /opt/k1/entrypoint.sh /opt/k1/probe_client.py /usr/local/bin/zap-baseline
USER 10001:10001
WORKDIR /work
# Loopback inside the container only; nothing is published to the host.
EXPOSE 8081
ENTRYPOINT ["/opt/k1/entrypoint.sh"]
CMD ["sleep", "infinity"]
