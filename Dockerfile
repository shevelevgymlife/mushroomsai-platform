FROM python:3.11

WORKDIR /app

COPY . .

RUN pip install --upgrade pip
RUN pip install -r requirements.txt

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "10000"]
