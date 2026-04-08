FROM python:3.11-slim

WORKDIR /app

# System deps:
# - cron: scheduler
# - build-essential/gcc/python3-dev: compile wheels like Pillow on armv7
# - image libs: needed by Pillow (ReportLab pulls Pillow)
RUN apt-get update && apt-get install -y --no-install-recommends \
    cron \
    build-essential \
    gcc \
    python3-dev \
    zlib1g-dev \
    libjpeg62-turbo-dev \
    libfreetype6-dev \
    liblcms2-dev \
    libwebp-dev \
    libopenjp2-7-dev \
    libtiff-dev \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt

# (optional but helpful) keep tooling current
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
  && pip install --no-cache-dir -r /app/requirements.txt

COPY generate_and_send.py /app/generate_and_send.py
COPY crontab.txt /etc/cron.d/hygro-reporter

# permissions + ensure newline
RUN chmod 0644 /etc/cron.d/hygro-reporter && sed -i -e '$a\' /etc/cron.d/hygro-reporter

# run cron in foreground so container stays alive
CMD ["cron", "-f"]
