FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Usuario sin privilegios
RUN useradd --create-home --uid 1000 quehacer && mkdir -p /srv/data/cache && chown -R quehacer /srv
USER quehacer

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
