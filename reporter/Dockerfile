FROM python:3.11-slim

WORKDIR /app

# install cron
RUN apt-get update && apt-get install -y --no-install-recommends cron \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY generate_and_send.py /app/generate_and_send.py
COPY crontab.txt /etc/cron.d/hygro-reporter

# permissions + ensure newline
RUN chmod 0644 /etc/cron.d/hygro-reporter && sed -i -e '$a\' /etc/cron.d/hygro-reporter

# run cron in foreground so container stays alive
CMD ["cron", "-f"]
