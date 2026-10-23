FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

WORKDIR /opt/m3u

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /opt/m3u/requirements.txt
RUN pip install --no-cache-dir -r /opt/m3u/requirements.txt

COPY scripts/ /opt/m3u/
COPY app.py /opt/strm-manager/app.py
COPY config.defaults.json /opt/strm-manager/config.defaults.json
COPY templates/ /opt/strm-manager/templates/
COPY static/ /opt/strm-manager/static/
COPY entrypoint.sh /usr/local/bin/strm-entrypoint
RUN chmod 0755 /usr/local/bin/strm-entrypoint

EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/strm-entrypoint"]
