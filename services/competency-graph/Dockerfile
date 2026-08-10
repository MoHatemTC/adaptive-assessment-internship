FROM python:3.12-slim

WORKDIR /srv

COPY services/contracts /srv/contracts
RUN pip install --no-cache-dir /srv/contracts

# No extras. This service needs no sandbox, no realtime voice and no tracing collector —
# it holds no session state, calls no model, and the only thing it reads is a graph.
COPY backend /srv/engine
RUN pip install --no-cache-dir /srv/engine

COPY services/common /srv/common
COPY services/clients /srv/clients
RUN pip install --no-cache-dir /srv/common /srv/clients

COPY services/competency-graph/requirements.txt /srv/requirements.txt
RUN pip install --no-cache-dir -r /srv/requirements.txt

COPY services/competency-graph/service /srv/service

EXPOSE 8083
RUN useradd --create-home --uid 10001 svc && chown -R svc /srv
USER svc

HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8083/health').status==200 else 1)"

CMD ["uvicorn", "service.main:app", "--host", "0.0.0.0", "--port", "8083"]
