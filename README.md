# Lorett Ground Link Monitor

Небольшой Python-скрипт для мониторинга станций приёма спутниковых данных.  
Скачивает логи, анализирует пролёты и отправляет ежедневный отчёт на email.

## Возможности
- Загрузка логов с серверов EUS
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

# статистика по коммерческим спутникам (список берётся из commercial_satellites в config.json)
# только станция (дата по умолчанию = сегодня)
python3 LorettGroundLinkMonitor.py --stat-commers R2.0S_Moscow

# станция + начало
python3 LorettGroundLinkMonitor.py --stat-commers R2.0S_Moscow 20260101

# станция + начало + конец
python3 LorettGroundLinkMonitor.py --stat-commers R2.0S_Moscow 20260101 20260110

# статистика по всем пролётам
python3 LorettGroundLinkMonitor.py --stat-all R2.0S_Moscow 20260101 20260110

# отключить отправку email (для ручных запусков)
python3 LorettGroundLinkMonitor.py --off-email 20260110
```

## Конфигурация
Все настройки находятся в `config.json`:
- станции (`name`, `bend` = `L`/`X`)
- email (SMTP, получатели)
- пороги SNR

## Systemd (сервис мониторинга)
Если скрипт установлен как systemd-сервис `lorett-monitor.service`, используйте:

```bash
# старт
sudo systemctl start lorett-monitor.service

# остановка
sudo systemctl stop lorett-monitor.service

# статус
systemctl status lorett-monitor.service --no-pager

# перезапуск
sudo systemctl restart lorett-monitor.service
```

## Логи
- `lorett_monitor.log`
- `journalctl` (при запуске через systemd)

## Лицензия
MIT
