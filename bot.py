import os
import json
import asyncio
import requests
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

load_dotenv("/workspace/telegram_bot/.env")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
COMFY_URL = "http://127.0.0.1:18188"
WORKFLOW = "/workspace/telegram_bot/BASE_API.json"
OUTPUT_DIR = "/workspace/ComfyUI/output"

queue_lock = asyncio.Lock()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отправь мне prompt.")


async def message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prompt = update.message.text

    if queue_lock.locked():
        await update.message.reply_text(
            "Предыдущая генерация ещё выполняется. "
            "Ваш prompt добавлен в очередь."
        )

    async with queue_lock:
        try:
            with open(WORKFLOW, "r", encoding="utf-8") as f:
                workflow = json.load(f)

            workflow["57:27"]["inputs"]["text"] = prompt

            response = requests.post(
                f"{COMFY_URL}/prompt",
                json={"prompt": workflow},
                timeout=30
            )

            data = response.json()
            prompt_id = data.get("prompt_id")

            if response.status_code != 200 or not prompt_id:
                await update.message.reply_text(
                    f"Ошибка ComfyUI:\n{response.text[:1000]}"
                )
                return

            await update.message.reply_text("Генерация началась...")

            for _ in range(120):
                await asyncio.sleep(2)

                history = requests.get(
                    f"{COMFY_URL}/history/{prompt_id}",
                    timeout=30
                ).json()

                job = history.get(prompt_id)

                if not job:
                    continue

                status = job.get("status", {}).get("status_str")

                if status == "error":
                    await update.message.reply_text(
                        "ComfyUI сообщил об ошибке."
                    )
                    return

                if status == "success":
                    outputs = job.get("outputs", {})

                    for node_output in outputs.values():
                        for image in node_output.get("images", []):
                            filename = image["filename"]
                            subfolder = image.get("subfolder", "")

                            path = os.path.join(
                                OUTPUT_DIR,
                                subfolder,
                                filename
                            )

                            if os.path.exists(path):
                                with open(path, "rb") as photo:
                                    await update.message.reply_photo(
                                        photo=photo,
                                        caption="Готово"
                                    )
                                return

                    await update.message.reply_text(
                        "Генерация завершена, но файл изображения не найден."
                    )
                    return

            await update.message.reply_text(
                "Генерация не завершилась за 4 минуты."
            )

        except Exception as e:
            await update.message.reply_text(f"Ошибка:\n{e}")


app = Application.builder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message))

print("Bot started")
app.run_polling()
