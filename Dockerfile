FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tzdata fonts-dejavu-core fonts-noto-color-emoji  \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./requirements.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

COPY main.py ./main.py
COPY libs/ ./libs/
COPY configs/ ./configs/
COPY avatar/ ./avatar/

RUN mkdir -p /app/data /app/avatar /app/configs

VOLUME ["/app/configs", "/app/avatar", "/app/data"]

ENTRYPOINT ["python", "/app/main.py"]
CMD []
