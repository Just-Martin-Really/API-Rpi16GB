# ── Build stage ───────────────────────────────────────────────────────────────
FROM debian:bookworm-slim AS builder

ARG ZIG_VERSION=0.16.0

RUN apt-get update && apt-get install -y \
    curl xz-utils libpq-dev ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Zig for aarch64 (RPi 5 is ARM64)
RUN curl -fsSL https://ziglang.org/download/${ZIG_VERSION}/zig-linux-aarch64-${ZIG_VERSION}.tar.xz \
    | tar -xJ -C /opt \
    && ln -s /opt/zig-linux-aarch64-${ZIG_VERSION}/zig /usr/local/bin/zig

WORKDIR /build
COPY build.zig .
COPY src/ src/

RUN zig build -Doptimize=ReleaseSafe

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y libpq5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /build/zig-out/bin/backend .

EXPOSE 8080
CMD ["./backend"]
