FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py .
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

RUN mkdir -p /app/data /app/certs

ENTRYPOINT ["./entrypoint.sh"]
