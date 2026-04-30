# Setup Guide — RPi 5 16 GB Backend

## How the Build and Deploy Pipeline Works

The Pi has internet access via the WLAN-AP's NAT. Docker builds the backend image directly on the Pi using a multi-stage build — the builder stage downloads Zig 0.16.0, compiles the source, and the runtime stage copies only the binary.

```
Pi
  └─ docker compose up --build
       ├─ builder stage: downloads zig-aarch64-linux-0.16.0, compiles src/ → binary
       └─ runtime stage: copies binary + libpq5 → running container
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

Connect the Pi directly to your Mac via ethernet (internet sharing enabled on Mac), then:

```sh
ssh <username>@backend-server.local
sudo apt update && sudo apt upgrade -y
```

If `backend-server.local` doesn't resolve, find the IP via your router or direct ethernet link-local address.

### 1.3 Enable WiFi

The onboard WiFi adapter may be disabled on first boot. Enable it and set the regulatory domain:

```sh
sudo iw reg set DE
sudo nmcli radio wifi on
```

### 1.4 Connect to Production WLAN

```sh
sudo nmcli device wifi connect "Production" password "<wifi-password>"
sudo nmcli connection modify "Production" connection.autoconnect yes
```

After this the Pi will reconnect to Production automatically on every boot. All further access is via the Production network (`192.168.50.x`).

### 1.5 Disable WiFi Power Saving

By default the Pi's WiFi chip enters power save mode, which causes it to miss incoming frames — making it unreachable from the network while still being able to initiate outbound connections. Disable it permanently:

```sh
printf '[connection]\nwifi.powersave = 2\n' | sudo tee /etc/NetworkManager/conf.d/wifi-powersave.conf && sudo systemctl reload NetworkManager
```

### 1.6 Install Docker

```sh
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
```

Log out and back in, then verify:

```sh
docker run --rm hello-world
```

### 1.7 Install Git and Clone the Repo

```sh
sudo apt install -y git
git clone https://github.com/Just-Martin-Really/API-Rpi16GB.git ~/API-Rpi16GB
```

### 1.8 Create Secrets

Passwords are never stored in the repo. Generate them on the Pi and store in `docker/secrets/` (gitignored):

```sh
mkdir -p ~/API-Rpi16GB/docker/secrets
chmod 700 ~/API-Rpi16GB/docker/secrets

# DB passwords
echo "$(openssl rand -base64 24)" > ~/API-Rpi16GB/docker/secrets/db_password.txt
echo "$(openssl rand -base64 24)" > ~/API-Rpi16GB/docker/secrets/db_write_password.txt
echo "$(openssl rand -base64 24)" > ~/API-Rpi16GB/docker/secrets/db_read_password.txt

# JWT signing key
echo "$(openssl rand -base64 48)" > ~/API-Rpi16GB/docker/secrets/jwt_secret.txt

# MQTT controller credentials
echo "controller"                 > ~/API-Rpi16GB/docker/secrets/mqtt_controller_user.txt
echo "$(openssl rand -base64 24)" > ~/API-Rpi16GB/docker/secrets/mqtt_controller_password.txt

# MQTT sensor01 credentials — copy this password into the Pico's main.py
echo "$(openssl rand -base64 24)" > ~/API-Rpi16GB/docker/secrets/mqtt_sensor01_password.txt
echo "Sensor01 MQTT password (put this in the Pico's MQTT_PW):"
cat ~/API-Rpi16GB/docker/secrets/mqtt_sensor01_password.txt

chmod 600 ~/API-Rpi16GB/docker/secrets/*.txt
```

The CA cert (`ca_cert.txt`) is written automatically by `setup_tls.sh` in the next step.

### 1.9 Provision TLS Certificates

Run the TLS setup script. It creates a local CA, signs a cert for nginx (`backend.lab.local`) and a cert for the MQTT broker, and drops the CA cert into `docker/secrets/` for the controller container.

```sh
cd ~/API-Rpi16GB
sudo sh docker/setup_tls.sh
```

Output:
- `/etc/ssl/backend/backend.crt` and `backend.key` — mounted by nginx
- `docker/mosquitto/ssl/broker.crt`, `broker.key`, `ca.crt` — mounted by mosquitto
- `docker/secrets/ca_cert.txt` — mounted into the controller container for TLS verification

Copy the CA cert to the Pico and to any client browser that will connect to the dashboard so they trust the self-signed CA.

### 1.10 Host Firewall (iptables)

Docker manages its own iptables rules for the bridge networks automatically. Additionally, restrict SSH access on the host:

```sh
sudo iptables -A INPUT -p tcp --dport 22 -s 192.168.50.0/24 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 22 -j DROP
sudo apt install iptables-persistent -y
sudo netfilter-persistent save
```

---

## Part 2 — Starting the Stack

```sh
cd ~/API-Rpi16GB/docker
docker compose up --build -d
```

The first run takes several minutes — Docker downloads Zig 0.16.0 (~90 MB), compiles the backend, and pulls postgres and nginx. Subsequent runs use the layer cache and are much faster.

After the first start, set DB and MQTT passwords:

```sh
sh set_passwords.sh
```

This sets the DB user passwords via `psql`, generates the mosquitto `passwd` file from the MQTT secrets, and restarts the controller.

Check status:

```sh
docker compose ps
docker compose logs -f backend
```

### Verify

From any device on the Production WLAN (`192.168.50.0/24`):

```sh
curl -k https://192.168.50.<backend-ip>/health
# → {"status":"ok"}
```

---

## Part 3 — Updating the Backend

After pushing a code change, pull and rebuild on the Pi:

```sh
cd ~/API-Rpi16GB && git pull
cd docker && docker compose up --build -d --no-deps backend
```

`--no-deps` rebuilds only the backend container without touching postgres or nginx.

---

## Part 4 — Local Development (Dev Machine)

Install Zig 0.16.0 from https://ziglang.org/download/ — extract and add to PATH:

```sh
echo 'export PATH="/Users/<you>/Library/zig/0.16.0:$PATH"' >> ~/.zshrc
source ~/.zshrc
zig version  # should print 0.16.0
```

Install libpq for local builds:

```sh
brew install libpq
brew link libpq --force
```

Build and run locally:

```sh
make run
```

### Zig 0.16.0 API Notes

0.16.0 has breaking changes from earlier versions relevant to this project:

| Old | New (0.16.0) |
|-----|-------------|
| `std.heap.GeneralPurposeAllocator` | `std.heap.DebugAllocator` |
| `std.net.Address.parseIp(...)` | `std.Io.net.IpAddress.parse(...)` |
| `address.listen(.{})` | `address.listen(io, .{})` — requires `std.Io` instance |
| `std.net.Server.Connection` | `std.Io.net.Stream` |
| `std.http.Server.init(connection, &buf)` | `std.http.Server.init(&reader.interface, &writer.interface)` |
| `request.reader()` | `request.readerExpectNone(&buf)` |
| `reader.readAll(&buf)` | `reader.readSliceShort(&buf)` |
| `std.fs.openFileAbsolute(...)` | removed — use C `fopen` via `@cImport(@cInclude("stdio.h"))` |
| `std.fs.cwd()` | removed — same workaround as above |
| `std.mem.trimRight(T, slice, chars)` | removed — inline the loop |
| `std.ArrayList(T).init(allocator)` | removed — use a heap-allocated fixed buffer or `ArrayListUnmanaged` |
| `@cInclude("postgresql/libpq-fe.h")` | use `@cInclude("libpq-fe.h")` — include path set per-platform in `build.zig` |
| `std.time.timestamp()` | removed — use C `time(null)` via `@cImport(@cInclude("time.h"))` |
| `std.base64.Base64Encoder.init(alphabet, pad)` | `alphabet` must be `[64]u8` not `*const [64:0]u8` — dereference with `.*` |
| `request.head.headers.getFirstValue(name)` | removed — iterate with `request.iterateHeaders()` |

Networking now requires a `std.Io.Threaded` instance — create it in `main` and pass `io` down to anything that does network I/O.

---

## Part 5 — Routine Operations

| Task | Command (on Pi, in `docker/`) |
|------|-------------------------------|
| View backend logs | `docker compose logs -f backend` |
| Restart backend only | `docker compose restart backend` |
| Stop everything | `docker compose down` |
| Stop and wipe DB | `docker compose down -v` ⚠️ destroys data |
| Check resource usage | `docker stats` |
| Check connected WLAN clients (on AP) | `sudo iw dev wlan0 station dump` |
| Check DHCP leases (on AP) | `cat /var/lib/misc/dnsmasq.leases` |
