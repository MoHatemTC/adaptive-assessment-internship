FROM python:3.12-slim

WORKDIR /srv

COPY services/contracts /srv/contracts
RUN pip install --no-cache-dir /srv/contracts

# No extras, and for the same reason `competency-graph` needs none: this service holds no
# session state, calls no model and runs nothing. It reads a graph and a list of item
# parameters, and returns a set of ids.
COPY backend /srv/engine
RUN pip install --no-cache-dir /srv/engine

COPY services/common /srv/common
COPY services/clients /srv/clients
RUN pip install --no-cache-dir /srv/common /srv/clients

COPY services/competency-scope/requirements.txt /srv/requirements.txt
RUN pip install --no-cache-dir -r /srv/requirements.txt

COPY services/competency-scope/service /srv/service

EXPOSE 8085
RUN useradd --create-home --uid 10001 svc && chown -R svc /srv
USER svc

HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8085/health').status==200 else 1)"

CMD ["uvicorn", "service.main:app", "--host", "0.0.0.0", "--port", "8085"]
