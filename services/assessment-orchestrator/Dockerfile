FROM python:3.12-slim

WORKDIR /srv

COPY services/contracts /srv/contracts
RUN pip install --no-cache-dir /srv/contracts

# `[tracing]` only. No sandbox client: this service must not be able to execute a
# submission even by mistake. No realtime-voice stack: it never opens a websocket.
#
# It DOES need model egress, which ADR-0001 said it would not — the Picking Agent calls a
# model on every queue fill. See ADR-0002; the failure mode of getting that wrong is
# selection silently falling back to the deterministic choice, which produces no error.
COPY backend /srv/engine
RUN pip install --no-cache-dir "/srv/engine[tracing]"

COPY services/common /srv/common
COPY services/clients /srv/clients
RUN pip install --no-cache-dir /srv/common /srv/clients

COPY services/assessment-orchestrator/requirements.txt /srv/requirements.txt
RUN pip install --no-cache-dir -r /srv/requirements.txt

COPY services/assessment-orchestrator/service /srv/service

EXPOSE 8080
# Non-root: this image serves candidate-facing traffic.
RUN useradd --create-home --uid 10001 svc && chown -R svc /srv
USER svc

HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/health').status==200 else 1)"

CMD ["uvicorn", "service.main:app", "--host", "0.0.0.0", "--port", "8080"]
