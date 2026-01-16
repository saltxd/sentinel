FROM python:3.11-slim

WORKDIR /app

# Install dependencies first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY sentinel/ ./sentinel/
COPY config.example.yaml ./
COPY healthcheck.py ./

# Create data directory for SQLite
RUN mkdir -p /app/data

# Run as non-root user
RUN useradd -m -u 1000 sentinel && \
    chown -R sentinel:sentinel /app
USER sentinel

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# Health check - runs every 60s, starts checking after 90s (allow startup time)
# Timeout after 10s, retries 3 times before marking unhealthy
HEALTHCHECK --interval=60s --timeout=10s --start-period=90s --retries=3 \
    CMD python /app/healthcheck.py || exit 1

CMD ["python", "-m", "sentinel.main"]
