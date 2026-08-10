FROM python:3.12-slim

WORKDIR /srv

COPY services/contracts /srv/contracts
RUN pip install --no-cache-dir /srv/contracts

# `[live]` and `[tracing]`. No sandbox client — nothing here executes a submission — and no
# grading path: a finished room produces a transcript, and turning that into a score is
# somebody else's job.
COPY backend /srv/engine
RUN pip install --no-cache-dir "/srv/engine[live,tracing]"

COPY services/common /srv/common
RUN pip install --no-cache-dir /srv/common

COPY services/live-voice/requirements.txt /srv/requirements.txt
RUN pip install --no-cache-dir -r /srv/requirements.txt

COPY services/live-voice/service /srv/service
COPY services/live-voice/static /srv/static

EXPOSE 8765
# Non-root: this image serves candidate-facing traffic and terminates their audio.
RUN useradd --create-home --uid 10001 svc && chown -R svc /srv
USER svc

HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8765/health').status==200 else 1)"

CMD ["uvicorn", "service.main:app", "--host", "0.0.0.0", "--port", "8765"]
