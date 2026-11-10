FROM python:3.13-slim
WORKDIR /app
# libopus: codec de audio que discord.py necesita para el temporizador de voz
RUN apt-get update && apt-get install -y --no-install-recommends libopus0 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "main.py"]