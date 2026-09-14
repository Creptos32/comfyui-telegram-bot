import os
import json
import asyncio
import random
import requests
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

load_dotenv("/workspace/telegram_bot/.env")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
COMFY_URL = "http://127.0.0.1:18188"
WORKFLOW = "/workspace/telegram_bot/BASE_API.json"
OUTPUT_DIR = "/workspace/ComfyUI/output"

PROMPT_NODE = "57:27"
KSAMPLER_NODE = "57:3"
LATENT_NODE = "57:13"

DEFAULT_SETTINGS = {
    "width": 512,
    "height": 512,
    "steps": 8,
    "cfg": 1.0,
    "seed": 0,
    "random_seed": False,
    "sampler_name": "res_multistep",
    "scheduler": "simple",
}

queue_lock = asyncio.Lock()


def get_settings(context: ContextTypes.DEFAULT_TYPE):
    settings = context.user_data.get("settings")

    if settings is None:
        settings = DEFAULT_SETTINGS.copy()
        context.user_data["settings"] = settings

    return settings


def settings_text(settings):
    seed = "random" if settings["random_seed"] else settings["seed"]

    return (
        "Текущие настройки:\n"
        f"Размер: {settings['width']}×{settings['height']}\n"
        f"Steps: {settings['steps']}\n"
        f"CFG: {settings['cfg']}\n"
        f"Seed: {seed}\n"
        f"Sampler: {settings['sampler_name']}\n"
        f"Scheduler: {settings['scheduler']}"
    )


def get_ksampler_choices():
    response = requests.get(
        f"{COMFY_URL}/object_info/KSampler",
        timeout=15,
    )
    response.raise_for_status()

    required = response.json()["KSampler"]["input"]["required"]
    samplers = required["sampler_name"][0]
    schedulers = required["scheduler"][0]

    return samplers, schedulers


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Отправь мне prompt.\n\n"
        "Команды:\n"
        "/settings — текущие настройки\n"
        "/size 1024 1024 — размер\n"
        "/steps 25 — число шагов\n"
        "/cfg 7 — CFG\n"
        "/seed 12345 или /seed random\n"
        "/samplers — доступные sampler и scheduler\n"
        "/sampler euler — sampler\n"
        "/scheduler normal — scheduler"
    )


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(settings_text(get_settings(context)))


async def size_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 2:
        await update.message.reply_text("Формат: /size ШИРИНА ВЫСОТА")
        return

    try:
        width, height = map(int, context.args)
    except ValueError:
        await update.message.reply_text("Ширина и высота должны быть целыми числами.")
        return

    if not (256 <= width <= 2048 and 256 <= height <= 2048):
        await update.message.reply_text("Допустимый размер: от 256 до 2048 пикселей.")
        return

    if width % 64 != 0 or height % 64 != 0:
        await update.message.reply_text("Ширина и высота должны быть кратны 64.")
        return

    settings = get_settings(context)
    settings["width"] = width
    settings["height"] = height

    await update.message.reply_text(f"Размер установлен: {width}×{height}")


async def steps_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("Формат: /steps ЧИСЛО")
        return

    try:
        steps = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Steps должны быть целым числом.")
        return

    if not 1 <= steps <= 100:
        await update.message.reply_text("Допустимое число steps: от 1 до 100.")
        return

    get_settings(context)["steps"] = steps
    await update.message.reply_text(f"Steps установлены: {steps}")


async def cfg_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("Формат: /cfg ЧИСЛО")
        return

    try:
        cfg = float(context.args[0])
    except ValueError:
        await update.message.reply_text("CFG должен быть числом, например: /cfg 7")
        return

    if not 0 <= cfg <= 30:
        await update.message.reply_text("Допустимый CFG: от 0 до 30.")
        return

    get_settings(context)["cfg"] = cfg
    await update.message.reply_text(f"CFG установлен: {cfg}")


async def seed_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("Формат: /seed ЧИСЛО или /seed random")
        return

    value = context.args[0].lower()
    settings = get_settings(context)

    if value == "random":
        settings["random_seed"] = True
        await update.message.reply_text("Seed будет случайным для каждой генерации.")
        return

    try:
        seed = int(value)
    except ValueError:
        await update.message.reply_text("Seed должен быть числом или словом random.")
        return

    if not 0 <= seed <= 2**64 - 1:
        await update.message.reply_text("Seed вне допустимого диапазона.")
        return

    settings["seed"] = seed
    settings["random_seed"] = False
    await update.message.reply_text(f"Seed установлен: {seed}")


async def samplers_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        samplers, schedulers = get_ksampler_choices()
        await update.message.reply_text(
            "Sampler:\n"
            + ", ".join(samplers)
            + "\n\nScheduler:\n"
            + ", ".join(schedulers)
        )
    except Exception as error:
        await update.message.reply_text(f"Не удалось получить список: {error}")


async def sampler_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("Формат: /sampler НАЗВАНИЕ\nИспользуйте /samplers.")
        return

    value = context.args[0]

    try:
        samplers, _ = get_ksampler_choices()
    except Exception as error:
        await update.message.reply_text(f"Не удалось проверить sampler: {error}")
        return

    if value not in samplers:
        await update.message.reply_text("Такого sampler нет. Используйте /samplers.")
        return

    get_settings(context)["sampler_name"] = value
    await update.message.reply_text(f"Sampler установлен: {value}")


async def scheduler_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("Формат: /scheduler НАЗВАНИЕ\nИспользуйте /samplers.")
        return

    value = context.args[0]

    try:
        _, schedulers = get_ksampler_choices()
    except Exception as error:
        await update.message.reply_text(f"Не удалось проверить scheduler: {error}")
        return

    if value not in schedulers:
        await update.message.reply_text("Такого scheduler нет. Используйте /samplers.")
        return

    get_settings(context)["scheduler"] = value
    await update.message.reply_text(f"Scheduler установлен: {value}")


async def message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prompt = update.message.text
    settings = get_settings(context).copy()

    if settings["random_seed"]:
        settings["seed"] = random.randint(0, 2**64 - 1)

    if queue_lock.locked():
        await update.message.reply_text(
            "Предыдущая генерация ещё выполняется. "
            "Ваш prompt добавлен в очередь."
        )

    async with queue_lock:
        try:
            with open(WORKFLOW, "r", encoding="utf-8") as file:
                workflow = json.load(file)

            workflow[PROMPT_NODE]["inputs"]["text"] = prompt

            workflow[KSAMPLER_NODE]["inputs"].update({
                "seed": settings["seed"],
                "steps": settings["steps"],
                "cfg": settings["cfg"],
                "sampler_name": settings["sampler_name"],
                "scheduler": settings["scheduler"],
            })

            workflow[LATENT_NODE]["inputs"].update({
                "width": settings["width"],
                "height": settings["height"],
            })

            response = requests.post(
                f"{COMFY_URL}/prompt",
                json={"prompt": workflow},
                timeout=30,
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
                    timeout=30,
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
                                filename,
                            )

                            if os.path.exists(path):
                                with open(path, "rb") as photo:
                                    await update.message.reply_photo(
                                        photo=photo,
                                        caption="Готово",
                                    )
                                return

                    await update.message.reply_text(
                        "Генерация завершена, но файл изображения не найден."
                    )
                    return

            await update.message.reply_text(
                "Генерация не завершилась за 4 минуты."
            )

        except Exception as error:
            await update.message.reply_text(f"Ошибка:\n{error}")


app = Application.builder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("settings", settings_command))
app.add_handler(CommandHandler("size", size_command))
app.add_handler(CommandHandler("steps", steps_command))
app.add_handler(CommandHandler("cfg", cfg_command))
app.add_handler(CommandHandler("seed", seed_command))
app.add_handler(CommandHandler("samplers", samplers_command))
app.add_handler(CommandHandler("sampler", sampler_command))
app.add_handler(CommandHandler("scheduler", scheduler_command))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message))

print("Bot started")
app.run_polling()