# syntax=docker/dockerfile:1

FROM python:3.12-slim AS base

# libcairo backs the optional PNG maths renderer; the DejaVu serif faces are
# what the generated cover is drawn with. Both are small and both are the
# difference between a working default and a silently degraded one.
RUN apt-get update && apt-get install --no-install-recommends -y \
        libcairo2 \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    XDG_CACHE_HOME=/cache

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --no-deps -e .

# Papers are fetched from the network and written to a mounted volume; there is
# no reason for any of that to happen as root.
RUN useradd --create-home --uid 1000 reader \
    && mkdir -p /out /cache \
    && chown -R reader:reader /out /cache /app
USER reader

VOLUME ["/out", "/cache"]

ENTRYPOINT ["arxiv2epub"]
CMD ["--help"]
