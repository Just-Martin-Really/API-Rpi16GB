FROM python:3.12-slim

RUN pip install --no-cache-dir psycopg2-binary==2.9.10

# Drop privileges: archiver only needs DB connectivity.
RUN groupadd -r app && useradd -r -g app -u 10001 app

WORKDIR /app
COPY archive_job.py .

USER app
CMD ["python", "archive_job.py"]
