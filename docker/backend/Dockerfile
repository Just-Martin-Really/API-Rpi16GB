# ── Build stage ───────────────────────────────────────────────────────────────
FROM debian:bookworm-slim AS builder

ARG ZIG_VERSION=0.16.0

RUN apt-get update && apt-get install -y \
    curl xz-utils libpq-dev ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Zig matching the current Docker build architecture
ARG TARGETARCH

RUN if [ "$TARGETARCH" = "arm64" ]; then \
        ZIG_ARCH="aarch64"; \
    elif [ "$TARGETARCH" = "amd64" ]; then \
        ZIG_ARCH="x86_64"; \
    else \
        echo "Unsupported architecture: $TARGETARCH" && exit 1; \
    fi \
    && curl -fsSL https://ziglang.org/download/${ZIG_VERSION}/zig-${ZIG_ARCH}-linux-${ZIG_VERSION}.tar.xz \
        | tar -xJ -C /opt \
    && ln -s /opt/zig-${ZIG_ARCH}-linux-${ZIG_VERSION}/zig /usr/local/bin/zig

WORKDIR /build
COPY build.zig .
ARG CACHE_BUST=1
COPY src/ src/

RUN zig build -Doptimize=ReleaseSafe

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

# Drop privileges: the Zig HTTP server listens on 8080 (unprivileged),
# reads DB passwords and CA cert from Docker secret mounts (world-readable),
# and has no need for root.
RUN groupadd -r app && useradd -r -g app -u 10001 app

WORKDIR /app
COPY --from=builder /build/zig-out/bin/backend .

USER app
EXPOSE 8080
CMD ["./backend"]
