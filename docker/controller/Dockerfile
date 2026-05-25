FROM python:3.12-slim

RUN pip install --no-cache-dir paho-mqtt==2.1.0 prometheus-client==0.20.0

WORKDIR /app
COPY controller.py .

CMD ["python", "controller.py"]
