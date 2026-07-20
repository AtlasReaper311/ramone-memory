FROM python:3.14-slim

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
