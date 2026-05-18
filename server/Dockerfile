FROM python:3.11-slim

WORKDIR /app

# Copiem fișierele necesare
COPY protocol.py .
COPY server.py .

EXPOSE 9999

CMD ["python", "server.py"]