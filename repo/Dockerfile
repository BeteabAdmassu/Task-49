FROM python:3.12-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN addgroup -S metroops && adduser -S -G metroops metroops

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
RUN mkdir -p /app/data/attachments && chown -R metroops:metroops /app

USER metroops

EXPOSE 5000

CMD ["python", "-m", "app.app"]
