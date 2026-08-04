FROM python:3.12-slim

WORKDIR /srv

# Contracts first: it changes least, so the layer caches.
COPY services/contracts /srv/contracts
RUN pip install --no-cache-dir /srv/contracts

COPY services/bank-registry/requirements.txt /srv/requirements.txt
RUN pip install --no-cache-dir -r /srv/requirements.txt

COPY services/bank-registry/app /srv/app

EXPOSE 8081
# Non-root: this image runs candidate-facing traffic.
RUN useradd --create-home --uid 10001 svc && chown -R svc /srv
USER svc

HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8081/health').status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8081"]
