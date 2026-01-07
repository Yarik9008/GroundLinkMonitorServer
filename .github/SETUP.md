# Настройка GitHub репозитория

## Шаги для подключения к GitHub

### 1. Создайте репозиторий на GitHub

1. Перейдите на https://github.com/new
2. Заполните:
   - **Repository name**: `LorettGroundLinkMonitor`
   - **Description**: `Система мониторинга станций приёма спутниковых данных`
   - **Visibility**: Private (рекомендуется, так как содержит конфигурацию)
   - **НЕ** инициализируйте с README, .gitignore или лицензией (уже есть)

### 2. Подключите локальный репозиторий к GitHub

```bash
cd /root/lorett/LorettGroundLinkMonitor

# Добавьте remote (замените YOUR_USERNAME на ваш GitHub username)
git remote add origin https://github.com/YOUR_USERNAME/LorettGroundLinkMonitor.git

# Или через SSH (если настроен SSH ключ):
# git remote add origin git@github.com:YOUR_USERNAME/LorettGroundLinkMonitor.git
```

### 3. Отправьте код на GitHub

```bash
# Отправьте код в ветку main
git push -u origin main
```

### 4. Проверка

Проверьте, что код успешно загружен:
```bash
git remote -v
git status
```

## Дополнительные настройки

### Настройка SSH ключа (опционально)

Если хотите использовать SSH вместо HTTPS:

1. Создайте SSH ключ (если еще нет):
```bash
ssh-keygen -t ed25519 -C "your_email@example.com"
```

2. Добавьте публичный ключ в GitHub:
   - Settings → SSH and GPG keys → New SSH key
   - Скопируйте содержимое `~/.ssh/id_ed25519.pub`

### Настройка GitHub Actions (опционально)

Можно добавить автоматические проверки через GitHub Actions. Пример файла `.github/workflows/test.yml`:

```yaml
name: Python Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.8'
      - name: Install dependencies
        run: |
          pip install -r requirements.txt
      - name: Check syntax
        run: |
          python3 -m py_compile LorettGroundLinkMonitor.py Logger.py
```

## Важные замечания

⚠️ **Безопасность:**
- Файл `config.json` с паролями **НЕ** попадет в репозиторий (добавлен в `.gitignore`)
- Используйте `config.json.example` как шаблон
- Если случайно закоммитили `config.json`, срочно смените пароли!

## Полезные команды

```bash
# Проверить статус
git status

# Посмотреть изменения
git diff

# Добавить изменения
git add .
git commit -m "Описание изменений"

# Отправить на GitHub
git push

# Получить обновления
git pull
```
