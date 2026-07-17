FROM python:3.11-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt pyproject.toml ./
COPY nexus_worker ./nexus_worker
RUN pip install --no-cache-dir .

ENV PYTHONUNBUFFERED=1
EXPOSE 8010

CMD ["nexus-worker", "run"]
