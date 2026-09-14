# ComfyUI Telegram Bot

Файлы для установки Telegram-бота на новом Vast.ai-инстансе.

## Что хранится здесь

- `bot.py` — код бота;
- `BASE_API.json` — workflow ComfyUI;
- `requirements.txt` — проверенные версии Python-библиотек;
- `setup_telegram_bot.ipynb` — установщик для нового инстанса;
- `.env.example` — шаблон без секретов.

## Установка на новом Vast.ai-инстансе

1. Запустите ComfyUI так, чтобы API был доступен по `127.0.0.1:18188`.
2. Откройте `setup_telegram_bot.ipynb` в JupyterLab.
3. Запускайте ячейки сверху вниз.
4. Когда notebook попросит токены, вставьте:
   - временный GitHub token с правом `Contents: Read-only` только для этого репозитория;
   - токен Telegram-бота.
5. После сообщения `Bot started in the background` отправьте боту тестовый prompt в Telegram.

## Безопасность

Никогда не добавляйте в репозиторий `.env`, Telegram-токен или GitHub token.
