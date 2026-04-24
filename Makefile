# Local dev targets — for building and running on the dev machine.
# Deployment on the Pi is done via 'docker compose up --build'.

.PHONY: build run

# Build for the local machine (not for the Pi)
build:
	zig build

# Build and run locally
run:
	zig build run
