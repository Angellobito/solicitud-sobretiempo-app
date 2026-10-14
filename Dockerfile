FROM python:3.11-bookworm
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TREMOND_HOSTED=1
COPY requirements-web.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py webapp.py ./
COPY web ./web
COPY assets/firma.png assets/plantilla.pdf ./assets/
EXPOSE 10000
CMD ["python", "webapp.py"]
