FROM python:3.12-slim

ARG APP_UID=10001
RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid ${APP_UID} --create-home --shell /usr/sbin/nologin app
WORKDIR /opt/ps3-media-automation
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY scripts ./scripts
COPY docker-entrypoint.sh ./docker-entrypoint.sh
COPY VERSION README.md LICENSE ./
RUN chmod 0755 scripts/*.py scripts/ps3ctl docker-entrypoint.sh && mkdir -p /srv/ps3-library /var/lib/ps3-media-automation && chown -R app:app /opt/ps3-media-automation /var/lib/ps3-media-automation
ENV PYTHONUNBUFFERED=1 APP_STATE_ROOT=/var/lib/ps3-media-automation PS3_LIBRARY_ROOT=/srv/ps3-library PS3_INCOMING_DIR=/srv/ps3-library/incoming PS3_WORK_ROOT=/srv/ps3-library/.iso-build-work PS3_IRD_ROOT=/srv/ps3-library/.ingest/irds PS3_ISO_ROOT=/srv/ps3-library/PS3ISO PS3_AUDIT_ROOT=/srv/ps3-library/.ingest/audits PS3_INGEST_COMMAND=/opt/ps3-media-automation/scripts/ps3_ingest.py PS3_IRD_PARSER_ROOT=/opt/ps3-tools/pyird
EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/healthz')"
ENTRYPOINT ["/opt/ps3-media-automation/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8787"]
