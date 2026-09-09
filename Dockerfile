FROM python:3.11-slim-buster

RUN apt-get update --fix-missing && \
    apt-get install -y --no-install-recommends \
        xvfb \
        wget \
        gnupg \
        chromium \
        chromium-driver \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV DISPLAY=:99
ENV CHROME_BIN=/usr/bin/chromium

CMD xvfb-run --auto-servernum --server-num=99 python main.py
