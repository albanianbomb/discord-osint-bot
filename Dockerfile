# Base Image mit Python 3.11
FROM python:3.11-slim

# Arbeitsverzeichnis
WORKDIR /app

# Kopiere alles in das Image
COPY . /app

# Upgrade pip und installiere Abhängigkeiten
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Expose für Ping (Flask)
EXPOSE 8080

# Startbefehl
CMD ["python", "bot.py"]
