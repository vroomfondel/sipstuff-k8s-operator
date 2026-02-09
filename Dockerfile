FROM python:3.14-slim

LABEL maintainer="Henning Thiess <ht@xomox.cc>"
LABEL description="sipstuff-k8s-operator — FastAPI service that creates SIP call K8s Jobs"

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY sipstuff_k8s_operator/ sipstuff_k8s_operator/

ENV PYTHONUNBUFFERED=1

EXPOSE 8080

CMD ["python3", "-m", "sipstuff_k8s_operator"]
