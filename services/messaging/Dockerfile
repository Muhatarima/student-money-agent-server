# Build from the REPO ROOT (it needs packages/contracts):
#   docker build -f services/messaging/Dockerfile -t messaging .
# One image, three Railway services (change the start command):
#   api (default) | python -m app.worker | python -m app.notifier
FROM python:3.12-slim

WORKDIR /srv/services/messaging
COPY packages/contracts /srv/packages/contracts
COPY services/messaging/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/messaging .

# Never run as root
RUN useradd --create-home app && chown -R app /srv
USER app

# Secrets come from the platform's environment variables, never from a file in the image
# Default 0.0.0.0 (IPv4). On Railway set HOST=:: (IPv6): its private network needs it
CMD ["sh", "-c", "uvicorn app.main:app --host ${HOST:-0.0.0.0} --port ${PORT:-8001}"]
