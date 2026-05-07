FROM python:3.12-slim

RUN pip install --no-cache-dir psycopg2-binary==2.9.10

WORKDIR /app
COPY seed_fake_data.py .

ENTRYPOINT ["python", "seed_fake_data.py"]
