FROM mcr.microsoft.com/devcontainers/python:3.12

# Настройка рабочего каталога
WORKDIR /workspace

# Копирование и установка зависимостей
COPY requirements.txt /workspace/requirements.txt
RUN pip install --no-cache-dir -r /workspace/requirements.txt

# Копирование всего содержимого (в том числе кода)
COPY . /workspace
