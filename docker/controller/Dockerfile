FROM python:3.12-slim

RUN pip install --no-cache-dir paho-mqtt==2.1.0 prometheus-client==0.20.0

# Drop privileges: controller binds the prometheus_client HTTP server on
# 8000 (unprivileged) and never opens raw sockets or filesystems beyond
# Docker secret mounts (which are world-readable by design).
RUN groupadd -r app && useradd -r -g app -u 10001 app

WORKDIR /app
COPY controller.py .

USER app
CMD ["python", "controller.py"]
