# Образование

Инструментът е в разработка. Чете всички публикувани учебни години за НВО VII клас от 2017/2018 нататък. Годините от 2021/2022 нататък се сверяват по код по НЕИСПУО с регистъра за същата година. За 2017/2018–2020/2021 г. официалните регистри нямат код по НЕИСПУО; резултатите се показват с изрично означение „без сверка с регистъра“. Има табло, списък с търсене и CSV, профил на училище с история, източници и методика. Останалите изпити и картата се добавят след отделна проверка на източниците.

## Проверка

От папката `tools/obrazovanie/` след `pip install -r requirements.txt` и с отделна PostgreSQL 17 база:

```bash
python -m ingest.run --step migrate
python -m ingest.run --step mon
python -m ingest.run --step history
python -m ingest.run --step legacy
python -m ingest.run --step freshness
uvicorn app.main:app --host 127.0.0.1 --port 8016
```

За тестовете се използва само база с `test` в името и променливата `OBRAZOVANIE_TEST_DSN`:

```bash
python -m pytest -q
python tests/mutations.py
```

Суровите отговори се пазят под `OBRAZOVANIE_DATA/raw/` (по подразбиране `/srv/prozrachnost/obrazovanie/raw/`). Уеб процесът използва `OBRAZOVANIE_DSN` с роля само за четене. Източниците и ограниченията са описани в [docs/sources.md](docs/sources.md) и [docs/methodology.md](docs/methodology.md).
