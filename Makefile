# Local dev targets — for building and running on the dev machine.
# Deployment on the Pi is done via 'docker compose up --build'.

.PHONY: build run test test-api

# Build for the local machine (not for the Pi)
build:
	zig build

# Build and run locally
run:
	zig build run

# Zig unit tests (no Docker needed)
test:
	zig build test

# HTTP integration tests — run on the 16GB Pi (requires: pip3 install requests pytest)
test-api:
	pytest tests/
