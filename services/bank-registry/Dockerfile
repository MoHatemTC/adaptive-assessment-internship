FROM python:3.12-slim

WORKDIR /srv

# Ordered by how often each layer changes, so the expensive ones cache.
#
# The ENGINE is installed as a library rather than vendored per service. There is one
# implementation of the psychometrics and four adapters over it; copying it four times
# would create four copies that drift. Which PART of it this service may import is not a
# packaging question — it is enforced by `services/tests/test_services.py`, which parses
# every import here against a declared slice.
COPY services/contracts /srv/contracts
RUN pip install --no-cache-dir /srv/contracts

COPY backend /srv/engine
RUN pip install --no-cache-dir /srv/engine

COPY services/common /srv/common
# The SQL bank store, and with it psycopg. Installed here and in bank-ingest only —
# every other service has no business holding a database driver, and keeping it out
# of the shared packages makes that a packaging fact rather than a convention.
COPY services/store /srv/store
RUN pip install --no-cache-dir /srv/common /srv/store

COPY services/bank-registry/requirements.txt /srv/requirements.txt
RUN pip install --no-cache-dir -r /srv/requirements.txt

COPY services/bank-registry/service /srv/service

# Banks posted at runtime. A volume, because they are content: registering one must not be
# a rebuild, and it must survive a restart.
ENV BANK_STORE_DIR=/srv/banks
RUN mkdir -p /srv/banks

EXPOSE 8081
# Non-root: this image serves candidate-facing traffic.
RUN useradd --create-home --uid 10001 svc && chown -R svc /srv
USER svc

HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8081/health').status==200 else 1)"

CMD ["uvicorn", "service.main:app", "--host", "0.0.0.0", "--port", "8081"]
