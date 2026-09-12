FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY emulator.py .

CMD ["python", "-u", "emulator.py"]

