FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY trade_bot/ ./trade_bot/

# The health endpoint must listen on all interfaces for Docker to reach it.
ENV HEALTH_HTTP_HOST=0.0.0.0 \
    HEALTH_HTTP_PORT=8080 \
    PYTHONUNBUFFERED=1

EXPOSE 8080

# Probes the running process through its own health endpoint, so a wedged
# trading loop marks the container unhealthy - not just an unreachable API.
HEALTHCHECK --interval=60s --timeout=15s --start-period=30s --retries=3 \
    CMD python -m trade_bot --healthcheck || exit 1

CMD ["python", "-m", "trade_bot"]
