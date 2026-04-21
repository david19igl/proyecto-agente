# =============================================================================
# Dockerfile — imagen base compartida por todos los servicios
# =============================================================================

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# cron para el reporte semanal, curl para el healthcheck del servicio web
RUN apt-get update && apt-get install -y --no-install-recommends \
        cron \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Dependencias Python (capa cacheada si requirements.txt no cambia)
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Código fuente
COPY . .

RUN mkdir -p /app/logs

# El comando lo sobreescribe cada servicio en docker-compose.yml
CMD ["python", "--version"]