FROM python:3.12-slim

RUN pip install --no-cache-dir paho-mqtt==2.1.0 psycopg2-binary==2.9.10

WORKDIR /app
COPY controller.py .

CMD ["python", "controller.py"]
