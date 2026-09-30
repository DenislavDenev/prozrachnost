# Образование

Инструментът е в разработка. Чете всички публикувани учебни години за НВО IV, VII и X клас, 16 ресурса за матури от 2015/2016 до 2025/2026 г. и кодовите списъци за защитени и средищни училища. Годините от 2021/2022 нататък се сверяват по код по НЕИСПУО с регистъра за същата година. По-старите резултати са означени като несверени. Има табло, списъци с търсене и CSV, профил на училище с история и статут по години, изпити по година и предмет, източници и методика. Картата и данните за брой ученици, профили и бюджети се добавят след отделна проверка на източниците.

## Проверка

От папката `tools/obrazovanie/` след `pip install -r requirements.txt` и с отделна PostgreSQL 17 база:

```bash
python -m ingest.run --step migrate
python -m ingest.run --step mon
python -m ingest.run --step history
python -m ingest.run --step legacy
python -m ingest.run --step dzi
python -m ingest.run --step nvo
python -m ingest.run --step status
python -m ingest.run --step freshness
uvicorn app.main:app --host 127.0.0.1 --port 8016
```

За тестовете се използва само база с `test` в името и променливата `OBRAZOVANIE_TEST_DSN`:

```bash
python -m pytest -q
python tests/mutations.py
```

Суровите отговори се пазят под `OBRAZOVANIE_DATA/raw/` (по подразбиране `/srv/prozrachnost/obrazovanie/raw/`). Уеб процесът използва `OBRAZOVANIE_DSN` с роля само за четене. Източниците и ограниченията са описани в [docs/sources.md](docs/sources.md) и [docs/methodology.md](docs/methodology.md).
