FROM python:3.12-slim@sha256:090ba77e2958f6af52a5341f788b50b032dd4ca28377d2893dcf1ecbdfdfe203

# Runtime libs TensorFlow needs on slim images.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -r app && useradd -r -g app -u 10001 app

WORKDIR /app

COPY lstm/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY lstm/ ./

USER app
CMD ["python", "control_loop.py"]
