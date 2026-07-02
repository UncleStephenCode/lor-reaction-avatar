FROM python:3.14-slim

ARG APP_UID=1000
ARG APP_GID=1000

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/app

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        tzdata \
        fonts-dejavu-core \
        fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "${APP_GID}" app \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" --home-dir /app --shell /usr/sbin/nologin app

COPY requirements.txt ./requirements.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

COPY --chown=app:app main.py ./main.py
COPY --chown=app:app libs/ ./libs/
COPY --chown=app:app configs/ ./configs/
COPY --chown=app:app avatar/ ./avatar/

RUN mkdir -p /app/data /app/avatar /app/configs \
    && chown -R app:app /app \
    && chmod -R u+rwX,g+rwX /app/data /app/avatar /app/configs

USER app:app

ENTRYPOINT ["python", "/app/main.py"]
CMD []
