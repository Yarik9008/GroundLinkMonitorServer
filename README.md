# Lorett Ground Link Monitor

Небольшой Python-скрипт для мониторинга станций приёма спутниковых данных.  
Скачивает логи, анализирует пролёты и отправляет ежедневный отчёт на email.

## Возможности
- Загрузка логов с серверов EUS (`oper` / `reg`)
- Анализ SNR и определение пустых пролётов
- Генерация графиков (по дням и за 7 дней)
- Автоматическая отправка отчётов на email
- Работа по расписанию (00:00 UTC)

## Установка
```bash
pip install -r requirements.txt
```

## Использование
```bash
# текущая дата (UTC)
python3 LorettGroundLinkMonitor.py

# конкретная дата
python3 LorettGroundLinkMonitor.py 20260107

# диапазон дат
python3 LorettGroundLinkMonitor.py 20260101 20260107

# ежедневный запуск
python3 LorettGroundLinkMonitor.py --scheduler
```

## Конфигурация
Все настройки находятся в `config.json`:
- станции (`name`, `oper/reg`, `X/L`)
- email (SMTP, получатели)
- пороги SNR

## Логи
- `lorett_monitor.log`
- `journalctl` (при запуске через systemd)

## Лицензия
MIT
