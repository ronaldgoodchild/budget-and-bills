FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV BUDGET_APP_HOST=0.0.0.0
ENV BUDGET_APP_PORT=5000

EXPOSE 5000

VOLUME ["/app/data"]

CMD ["python", "app.py"]
