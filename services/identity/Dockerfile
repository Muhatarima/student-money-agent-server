# Build from the REPO ROOT (it needs packages/contracts):
#   docker build -f services/identity/Dockerfile -t identity .
# Two Railway services from this image: api (default) | python -m app.jobs
FROM python:3.12-slim

WORKDIR /srv/services/identity
COPY packages/contracts /srv/packages/contracts
COPY services/identity/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# app/, alembic/, templates/, static/ (see Dockerfile.dockerignore for what stays out)
COPY services/identity .

# Never run as root
RUN useradd --create-home app && chown -R app /srv
USER app

# Secrets come from the platform's environment variables, never from a file in the image.
# --proxy-headers + --forwarded-allow-ips: behind Railway's proxy, use the real client IP
# (rate limits) and https scheme. Only the proxy can reach the container.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8003} --proxy-headers --forwarded-allow-ips='*'"]
