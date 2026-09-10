FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea

# No bytecode files in the image layers; logs flush immediately so
# `docker compose logs -f` shows startup progress in real time.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv

# Dependencies first: this layer only rebuilds when the pins change,
# not on every source edit.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Non-root: the service needs no filesystem writes at all, so it gets
# no privileges to make any.
RUN useradd --create-home --uid 10001 ramone \
    && chown -R ramone /srv
USER ramone

EXPOSE 8091
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8091"]
