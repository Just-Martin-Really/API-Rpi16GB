FROM python:3.12-slim@sha256:090ba77e2958f6af52a5341f788b50b032dd4ca28377d2893dcf1ecbdfdfe203

RUN pip install --no-cache-dir psycopg2-binary==2.9.10

RUN groupadd -r app && useradd -r -g app -u 10001 app

WORKDIR /app
COPY seed_fake_data.py .

USER app
ENTRYPOINT ["python", "seed_fake_data.py"]
