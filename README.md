# Save Video

Web-сервис для скачивания видео с YouTube.

## Возможности

- Скачивание видео с YouTube в разных форматах и качестве
- Извлечение аудио в MP3
- Прогресс-бар скачивания
- Адаптивный веб-интерфейс

## Запуск

### Docker (рекомендуется)

```bash
docker compose up -d
```

Сервис будет доступен на `http://localhost:5000`

### Без Docker

```bash
# Требуется ffmpeg
pip install -r requirements.txt
python app.py
```

## Стек

- Python + Flask
- yt-dlp
- Vanilla JS фронтенд
