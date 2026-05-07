FROM python:3.12-slim

RUN pip install --no-cache-dir psycopg2-binary==2.9.10

WORKDIR /app
COPY archive_job.py .

CMD ["python", "archive_job.py"]
