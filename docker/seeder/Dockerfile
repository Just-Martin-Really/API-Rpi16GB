FROM python:3.12-slim

RUN pip install --no-cache-dir psycopg2-binary==2.9.10

RUN groupadd -r app && useradd -r -g app -u 10001 app

WORKDIR /app
COPY seed_fake_data.py .

USER app
ENTRYPOINT ["python", "seed_fake_data.py"]
