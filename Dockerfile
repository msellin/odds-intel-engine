FROM python:3.12-slim

WORKDIR /app
ENV TZ=UTC
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

CMD ["python", "-m", "workers.scheduler"]
