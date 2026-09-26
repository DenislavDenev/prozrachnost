# Прозрачност

Лек хъб за публични инструменти с дизайна „София“ и слогана „По-ясна картина за България“. Първата версия показва само **Обществени поръчки** и **Пътна обстановка**. Няма избор на община.

## Стартиране

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
ROAD_URL=http://localhost:8002 TENDER_URL=https://tender.denev.work .venv/bin/uvicorn app:app --host 0.0.0.0 --port 8001
```

На Windows използвай `.venv\\Scripts\\python -m uvicorn app:app --port 8001`.

Настройки: `ROAD_URL`, `TENDER_URL`. Проверка: `/healthz`. Няма собствена база данни или външни заявки при отваряне на страницата. Шрифтовете Sofia Sans се сервират локално.

## Публикуване

Хъбът може да стои на същия LXC като Tender, но на самостоятелен порт 8001. Добави отделен Proxy Host в Nginx Proxy Manager към `192.168.1.68:8001` и DNS запис за избрания домейн. Това не изисква втори LXC или втори публичен IP адрес. Задай `ROAD_URL` на публичния адрес на пътния инструмент.

Лиценз на шрифта: [SIL OFL](static/fonts/OFL.txt).
