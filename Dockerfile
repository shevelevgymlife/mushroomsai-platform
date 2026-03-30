FROM node:20-alpine AS call-build
WORKDIR /src/frontend/call
COPY frontend/call/package.json frontend/call/package-lock.json* ./
RUN npm install
COPY frontend/call/ ./
RUN npm run build

FROM python:3.11

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .

RUN pip install --upgrade pip
RUN pip install -r requirements.txt

COPY . .
COPY --from=call-build /src/static/call ./static/call

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "10000"]
