FROM python:3.12-slim

WORKDIR /srv

COPY services/contracts /srv/contracts
RUN pip install --no-cache-dir /srv/contracts

# No extras. Ingest runs nothing, calls no model and holds no session: it parses a file,
# derives a graph from it and writes a bank.
COPY backend /srv/engine
RUN pip install --no-cache-dir /srv/engine

COPY services/common /srv/common
# The SQL bank store, and with it psycopg. Installed here and in bank-ingest only —
# every other service has no business holding a database driver, and keeping it out
# of the shared packages makes that a packaging fact rather than a convention.
COPY services/store /srv/store
RUN pip install --no-cache-dir /srv/common /srv/store

COPY services/bank-ingest/requirements.txt /srv/requirements.txt
RUN pip install --no-cache-dir -r /srv/requirements.txt

COPY services/bank-ingest/service /srv/service

# The same store bank-registry reads. A volume, because registering a bank must not be a
# rebuild and must survive a restart.
ENV BANK_STORE_DIR=/srv/banks
RUN mkdir -p /srv/banks

EXPOSE 8084
RUN useradd --create-home --uid 10001 svc && chown -R svc /srv
USER svc

HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8084/health').status==200 else 1)"

CMD ["uvicorn", "service.main:app", "--host", "0.0.0.0", "--port", "8084"]
