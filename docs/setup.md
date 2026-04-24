# Setup Guide — RPi 5 16 GB Backend

## How the Build and Deploy Pipeline Works

The Pi has internet access via the WLAN-AP's NAT. Docker builds the backend image directly on the Pi using a multi-stage build — the builder stage downloads Zig, compiles the source, and the runtime stage copies only the binary.

```
Pi
  └─ docker compose up --build
       ├─ builder stage: downloads Zig, compiles src/ → binary
       └─ runtime stage: copies binary → running container
```

---

## Part 1 — One-Time Pi Setup

### 1.1 Flash the SD Card

Use Raspberry Pi Imager on your dev machine:

- Model: Raspberry Pi 5
- OS: Raspberry Pi OS Lite (64-bit)
- Before writing, open settings and configure:
  - Hostname: `backend-server`
  - Enable SSH
  - Username: choose a non-default name (not `admin`, `pi`, `root`)
  - Password: >8 characters, at least one number and one special character
  - Locale: Europe/Berlin, keyboard: de

### 1.2 First Boot and System Update

```sh
ssh <username>@backend-server.local

sudo apt update && sudo apt upgrade -y
```

### 1.3 Install Docker

```sh
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
```

Log out and back in for the group change to take effect, then verify:

```sh
docker run --rm hello-world
```

### 1.4 Clone the Repo

```sh
git clone <repo-url> ~/API-Rpi16GB
cd ~/API-Rpi16GB
```

### 1.5 Create Secrets

Passwords are never stored in the repo. Create them manually on the Pi:

```sh
mkdir -p ~/API-Rpi16GB/docker/secrets
chmod 700 ~/API-Rpi16GB/docker/secrets

echo "your_postgres_superuser_password" > ~/API-Rpi16GB/docker/secrets/db_password.txt
echo "your_write_user_password"         > ~/API-Rpi16GB/docker/secrets/db_write_password.txt
echo "your_read_user_password"          > ~/API-Rpi16GB/docker/secrets/db_read_password.txt

chmod 600 ~/API-Rpi16GB/docker/secrets/*.txt
```

> The `CHANGEME_WRITE` and `CHANGEME_READ` placeholders in `docker/postgres/init.sql` must be replaced with the same passwords before the first `docker compose up`. Edit the file on the Pi:
> ```sh
> nano ~/API-Rpi16GB/docker/postgres/init.sql
> ```

### 1.6 Provision TLS Certificate

nginx expects a certificate and key at `/etc/ssl/backend/` on the Pi host.

```sh
sudo mkdir -p /etc/ssl/backend
```

For testing, generate a self-signed certificate:

```sh
sudo openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout /etc/ssl/backend/backend.key \
  -out    /etc/ssl/backend/backend.crt \
  -subj "/CN=backend-server"
```

For production, copy the certificate issued by the project CA from the WLAN-AP.

### 1.7 Host Firewall (iptables)

Docker manages its own iptables rules for the bridge networks automatically. Additionally, restrict SSH access on the host:

```sh
# Allow SSH only from the Production WLAN subnet
sudo iptables -A INPUT -p tcp --dport 22 -s 192.168.50.0/24 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 22 -j DROP

# Persist across reboots
sudo apt install iptables-persistent -y
sudo netfilter-persistent save
```

---

## Part 2 — Starting the Stack

```sh
cd ~/API-Rpi16GB/docker
docker compose up --build -d
```

The first run takes a few minutes — Docker downloads Zig, compiles the backend, and pulls postgres and nginx. Subsequent runs use the layer cache and are much faster.

Check status:

```sh
docker compose ps
docker compose logs -f backend
docker compose logs -f postgres
```

### Verify

From another host on the Production WLAN:

```sh
curl -k https://backend-server.local/health
# → {"status":"ok"}
```

---

## Part 3 — Updating the Backend

After pushing a code change, pull and rebuild on the Pi:

```sh
cd ~/API-Rpi16GB
git pull
cd docker
docker compose up --build -d --no-deps backend
```

`--no-deps` rebuilds only the backend container without touching postgres or nginx.

---

## Part 4 — Local Development (Dev Machine)

Install Zig 0.16.0 from https://ziglang.org/download/ and add it to your PATH.

Install libpq for local builds:

```sh
brew install libpq
brew link libpq --force
```

Build and run locally (targets your Mac, not the Pi):

```sh
make run
```

---

## Part 5 — Routine Operations

| Task | Command (on Pi, in `docker/`) |
|------|-------------------------------|
| View backend logs | `docker compose logs -f backend` |
| Restart backend only | `docker compose restart backend` |
| Stop everything | `docker compose down` |
| Stop and wipe DB | `docker compose down -v` ⚠️ destroys data |
| Check resource usage | `docker stats` |
