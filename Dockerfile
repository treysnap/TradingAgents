FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY . .

RUN pip install --no-cache-dir . && \
    pip install --no-cache-dir -r requirements.txt

CMD ["python", "start.py"]
