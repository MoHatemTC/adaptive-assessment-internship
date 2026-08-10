FROM python:3.12-slim

WORKDIR /srv

COPY services/contracts /srv/contracts
RUN pip install --no-cache-dir /srv/contracts

# `[sandbox]` and `[tracing]`, and no `[live]`. The extras are the point: this is the only
# image that needs a sandbox client, and it does not need the realtime-voice stack. A
# service that cannot import a dependency cannot accidentally acquire the capability.
COPY backend /srv/engine
RUN pip install --no-cache-dir "/srv/engine[sandbox,tracing]"

COPY services/common /srv/common
COPY services/clients /srv/clients
RUN pip install --no-cache-dir /srv/common /srv/clients

COPY services/grader/requirements.txt /srv/requirements.txt
RUN pip install --no-cache-dir -r /srv/requirements.txt

COPY services/grader/service /srv/service

EXPOSE 8082
# Non-root: this image serves candidate-facing traffic. Note that untrusted candidate code
# runs in E2B, not here — nothing in this container executes a submission.
RUN useradd --create-home --uid 10001 svc && chown -R svc /srv
USER svc

HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8082/health').status==200 else 1)"

CMD ["uvicorn", "service.main:app", "--host", "0.0.0.0", "--port", "8082"]
