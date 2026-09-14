from huggingface_hub import hf_hub_download
from pathlib import Path
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
VIDEO_WORKFLOW = "/workspace/telegram_bot/MINIMAX_H3_API.json"
COMFY_INPUT_DIR = Path("/workspace/ComfyUI/input")

VIDEO_PROMPT_NODE = "138"
VIDEO_REF1_NODE = "137"
VIDEO_REF2_NODE = "139"
VIDEO_RATIOS = {
    "1:1": "1:1 (Square)",
    "2:3": "2:3 (Portrait Photo)",
    "3:2": "3:2 (Photo)",
    "3:4": "3:4 (Portrait Standard)",
    "4:3": "4:3 (Standard)",
    "9:16": "9:16 (Portrait Widescreen)",
    "16:9": "16:9 (Widescreen)",
    "21:9": "21:9 (Ultrawide)",
}

DEFAULT_VIDEO_SETTINGS = {
    "ratio": "16:9",
    "megapixels": 0.2,
    "duration": 3.0,
    "steps": 4,
    "seed": 431090200235557,
    "seed_mode": "fixed",
}

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
    
def get_video_settings(context):
        
    return context.user_data.setdefault(
        "video_settings",
        DEFAULT_VIDEO_SETTINGS.copy(),
    )


def video_settings_text(settings):
    seed = (
        settings["seed_mode"]
        if settings["seed_mode"] != "fixed"
        else settings["seed"]
    )

    return (
        "Video-настройки:\n"
        f"Aspect ratio: {settings['ratio']}\n"
        f"Megapixels: {settings['megapixels']}\n"
        f"Duration: {settings['duration']} sec\n"
        f"Steps: {settings['steps']}\n"
        f"Seed: {seed}"
    )


async def video_settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        video_settings_text(get_video_settings(context))
    )


async def ratio_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1 or context.args[0] not in VIDEO_RATIOS:
        await update.message.reply_text(
            "Формат: /ratio 1:1, 2:3, 3:2, 3:4, 4:3, 9:16, 16:9 или 21:9"
        )
        return

    ratio = context.args[0]
    get_video_settings(context)["ratio"] = ratio
    await update.message.reply_text(f"Aspect ratio установлен: {ratio}")


async def megapixels_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("Формат: /mp ЧИСЛО, например /mp 0.5")
        return

    try:
        megapixels = float(context.args[0])
    except ValueError:
        await update.message.reply_text("Megapixels должны быть числом.")
        return

    if not 0.1 <= megapixels <= 16.0:
        await update.message.reply_text("Допустимое значение: от 0.1 до 16.0 MP.")
        return

    get_video_settings(context)["megapixels"] = megapixels
    await update.message.reply_text(f"Megapixels установлены: {megapixels}")


async def duration_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("Формат: /duration СЕКУНДЫ")
        return

    try:
        duration = float(context.args[0])
    except ValueError:
        await update.message.reply_text("Длительность должна быть числом.")
        return

    if not 0.1 <= duration <= 30:
        await update.message.reply_text("Допустимая длительность: от 0.1 до 30 секунд.")
        return

    get_video_settings(context)["duration"] = duration
    await update.message.reply_text(f"Длительность установлена: {duration} sec")


async def video_steps_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("Формат: /vsteps ЧИСЛО")
        return

    try:
        steps = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Steps должны быть целым числом.")
        return

    if not 1 <= steps <= 20:
        await update.message.reply_text("Допустимое значение steps: от 1 до 20.")
        return

    get_video_settings(context)["steps"] = steps
    await update.message.reply_text(f"Video steps установлены: {steps}")


async def video_seed_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text(
            "Формат: /vseed ЧИСЛО, /vseed random или /vseed increment"
        )
        return

    value = context.args[0].lower()
    settings = get_video_settings(context)

    if value in ("random", "increment"):
        settings["seed_mode"] = value
        await update.message.reply_text(f"Video seed mode: {value}")
        return

    try:
        seed = int(value)
    except ValueError:
        await update.message.reply_text(
            "Используйте число, random или increment."
        )
        return

    if not 0 <= seed <= 2**64 - 1:
        await update.message.reply_text("Seed вне допустимого диапазона.")
        return

    settings["seed"] = seed
    settings["seed_mode"] = "fixed"
    await update.message.reply_text(f"Video seed установлен: {seed}")

def get_video_refs(context):
    return context.user_data.setdefault("video_refs", {})


async def set_image_commands(bot, chat_id):
    await bot.set_my_commands([
        ("mode", "Выбрать режим"),
        ("settings", "Настройки фото"),
        ("size", "Размер изображения"),
        ("steps", "Количество шагов"),
        ("cfg", "CFG"),
        ("seed", "Seed"),
        ("samplers", "Список sampler"),
        ("sampler", "Выбрать sampler"),
        ("scheduler", "Выбрать scheduler"),
    ], scope={"type": "chat", "chat_id": chat_id})


async def set_video_commands(bot, chat_id):
    await bot.set_my_commands([
        ("mode", "Выбрать режим"),
        ("video_settings", "Настройки видео"),
        ("ratio", "Соотношение сторон"),
        ("mp", "Разрешение видео"),
        ("duration", "Длительность"),
        ("vsteps", "Количество шагов"),
        ("vseed", "Seed видео"),
        ("ref1", "Первая референсная картинка"),
        ("ref2", "Вторая референсная картинка"),
        ("refs", "Показать референсы"),
        ("clearrefs", "Очистить референсы"),
    ], scope={"type": "chat", "chat_id": chat_id})


async def mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1 or context.args[0].lower() not in ("image", "video"):
        await update.message.reply_text("Формат: /mode image или /mode video")
        return

    mode = context.args[0].lower()
    context.user_data["mode"] = mode

    if mode == "video":
        await set_video_commands(context.bot, update.effective_chat.id)
    else:
        await set_image_commands(context.bot, update.effective_chat.id)

    await update.message.reply_text(f"Режим установлен: {mode}")

async def ref1_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["awaiting_ref"] = "ref1"
    await update.message.reply_text("Отправьте следующую картинку как Reference 1.")


async def ref2_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["awaiting_ref"] = "ref2"
    await update.message.reply_text("Отправьте следующую картинку как Reference 2.")


async def refs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    refs = get_video_refs(context)

    await update.message.reply_text(
        "Reference 1: " + ("загружен" if "ref1" in refs else "не загружен") + "\n"
        "Reference 2: " + ("загружен" if "ref2" in refs else "не загружен")
    )


async def clearrefs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("video_refs", None)
    context.user_data.pop("awaiting_ref", None)
    await update.message.reply_text("Выбранные референсы очищены.")


async def photo_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    slot = context.user_data.get("awaiting_ref")

    if slot not in ("ref1", "ref2"):
        await update.message.reply_text(
            "Сначала используйте /ref1 или /ref2, затем отправьте картинку."
        )
        return

    telegram_file = await update.message.photo[-1].get_file()

    user_id = update.effective_user.id
    relative_path = Path("telegram") / str(user_id) / f"{slot}.jpg"
    absolute_path = COMFY_INPUT_DIR / relative_path
    absolute_path.parent.mkdir(parents=True, exist_ok=True)

    await telegram_file.download_to_drive(custom_path=str(absolute_path))

    get_video_refs(context)[slot] = relative_path.as_posix()
    context.user_data.pop("awaiting_ref", None)

    reference_number = "1" if slot == "ref1" else "2"
    await update.message.reply_text(
        f"Reference {reference_number} сохранён. "
        "Используйте /refs для проверки."
    )

async def send_comfy_result(update, outputs):
    for node_output in outputs.values():
        for output_type in ("images", "gifs", "videos", "files"):
            for media in node_output.get(output_type, []):
                filename = media.get("filename")
                subfolder = media.get("subfolder", "")

                if not filename:
                    continue

                path = os.path.join(OUTPUT_DIR, subfolder, filename)

                if not os.path.exists(path):
                    continue

                extension = os.path.splitext(filename)[1].lower()

                with open(path, "rb") as media_file:
                    if extension in (".mp4", ".mov", ".mkv", ".webm"):
                       await update.message.reply_video(
    video=media_file,
    caption="Готово",
    connect_timeout=30,
    read_timeout=600,
    write_timeout=600,
    pool_timeout=30,
)
                    elif extension == ".gif":
                        await update.message.reply_animation(
                            animation=media_file,
                            caption="Готово",
                        )
                    else:
                        await update.message.reply_photo(
                            photo=media_file,
                            caption="Готово",
                        )

                return True

    return False

async def message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prompt = update.message.text
    mode = context.user_data.get("mode", "image")

    image_settings = get_settings(context).copy()
    video_settings = get_video_settings(context).copy()
    video_refs = get_video_refs(context).copy()

    if mode == "video":
        if video_settings["seed_mode"] == "random":
            video_settings["seed"] = random.randint(0, 2**64 - 1)
        elif video_settings["seed_mode"] == "increment":
            video_settings["seed"] = get_video_settings(context)["seed"]
            get_video_settings(context)["seed"] += 1
        missing_refs = [
            name for name in ("ref1", "ref2")
            if name not in video_refs
        ]

        if missing_refs:
            await update.message.reply_text(
                "Для video-режима сначала загрузите два референса: "
                "/ref1 и /ref2."
            )
            return
    elif image_settings["random_seed"]:
        image_settings["seed"] = random.randint(0, 2**64 - 1)

    if queue_lock.locked():
        await update.message.reply_text(
            "Предыдущая генерация ещё выполняется. "
            "Ваш prompt добавлен в очередь."
        )

    async with queue_lock:
        try:
            workflow_path = VIDEO_WORKFLOW if mode == "video" else WORKFLOW

            with open(workflow_path, "r", encoding="utf-8") as file:
                workflow = json.load(file)

            if mode == "video":
                video_prompt = (
                    "Use <Picture 1> and <Picture 2> as reference frames. "
                    "Keep their visual identity consistent.\n\n"
                    f"{prompt}"
                )

                workflow[VIDEO_PROMPT_NODE]["inputs"]["value"] = video_prompt
                workflow[VIDEO_REF1_NODE]["inputs"]["image"] = video_refs["ref1"]
                workflow[VIDEO_REF2_NODE]["inputs"]["image"] = video_refs["ref2"]
                workflow["115"]["inputs"].update({
                    "aspect_ratio": VIDEO_RATIOS[video_settings["ratio"]],
                    "megapixels": video_settings["megapixels"],
                })

                workflow["132"]["inputs"]["value"] = video_settings["duration"]
                workflow["143"]["inputs"]["value"] = video_settings["steps"]
                workflow["144"]["inputs"]["value"] = video_settings["steps"]
                workflow["129"]["inputs"]["noise_seed"] = video_settings["seed"]
                status_message = "Video-генерация началась..."
                max_checks = 600
            else:
                workflow[PROMPT_NODE]["inputs"]["text"] = prompt

                workflow[KSAMPLER_NODE]["inputs"].update({
                    "seed": image_settings["seed"],
                    "steps": image_settings["steps"],
                    "cfg": image_settings["cfg"],
                    "sampler_name": image_settings["sampler_name"],
                    "scheduler": image_settings["scheduler"],
                })

                workflow[LATENT_NODE]["inputs"].update({
                    "width": image_settings["width"],
                    "height": image_settings["height"],
                })

                status_message = "Генерация началась..."
                max_checks = 120

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

            await update.message.reply_text(status_message)

            for _ in range(max_checks):
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
                    sent = await send_comfy_result(
                        update,
                        job.get("outputs", {}),
                    )

                    if not sent:
                        await update.message.reply_text(
                            "Генерация завершена, но файл результата не найден."
                        )

                    return

            await update.message.reply_text(
                "Генерация не завершилась за 20 минут."
            )

        except Exception as error:
            await update.message.reply_text(f"Ошибка:\n{error}")

async def post_init(application):
    await application.bot.set_my_commands([
        ("start", "Запустить бота"),
        ("mode", "Выбрать фото или видео"),
        ("settings", "Настройки фото"),
        ("size", "Размер изображения"),
        ("steps", "Количество шагов"),
        ("cfg", "CFG"),
        ("seed", "Seed"),
        ("samplers", "Список sampler"),
        ("sampler", "Выбрать sampler"),
        ("scheduler", "Выбрать scheduler"),
        ("video_settings", "Настройки видео"),
        ("ratio", "Соотношение сторон видео"),
        ("mp", "Разрешение видео"),
        ("duration", "Длительность видео"),
        ("vsteps", "Шаги видео"),
        ("vseed", "Seed видео"),
        ("ref1", "Первая референсная картинка"),
        ("ref2", "Вторая референсная картинка"),
        ("refs", "Показать референсы"),
        ("clearrefs", "Очистить референсы"),
    ])

app = Application.builder().token(TOKEN).post_init(post_init).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("settings", settings_command))
app.add_handler(CommandHandler("size", size_command))
app.add_handler(CommandHandler("steps", steps_command))
app.add_handler(CommandHandler("cfg", cfg_command))
app.add_handler(CommandHandler("seed", seed_command))
app.add_handler(CommandHandler("samplers", samplers_command))
app.add_handler(CommandHandler("sampler", sampler_command))
app.add_handler(CommandHandler("scheduler", scheduler_command))
app.add_handler(CommandHandler("mode", mode_command))
app.add_handler(CommandHandler("ref1", ref1_command))
app.add_handler(CommandHandler("ref2", ref2_command))
app.add_handler(CommandHandler("refs", refs_command))
app.add_handler(CommandHandler("clearrefs", clearrefs_command))
app.add_handler(CommandHandler("video_settings", video_settings_command))
app.add_handler(CommandHandler("ratio", ratio_command))
app.add_handler(CommandHandler("mp", megapixels_command))
app.add_handler(CommandHandler("duration", duration_command))
app.add_handler(CommandHandler("vsteps", video_steps_command))
app.add_handler(CommandHandler("vseed", video_seed_command))
HF_TOKEN_FILE = Path("/workspace/telegram_bot/.hf_token")


def save_hf_token(token):
    HF_TOKEN_FILE.write_text(token.strip())
    HF_TOKEN_FILE.chmod(0o600)


def load_hf_token():
    if HF_TOKEN_FILE.exists():
        return HF_TOKEN_FILE.read_text().strip()
    return None


CIVITAI_TOKEN_FILE = Path("/workspace/telegram_bot/.civitai_token")


def save_civitai_token(token):
    CIVITAI_TOKEN_FILE.write_text(token.strip())
    CIVITAI_TOKEN_FILE.chmod(0o600)


def load_civitai_token():
    if CIVITAI_TOKEN_FILE.exists():
        return CIVITAI_TOKEN_FILE.read_text().strip()
    return None


async def download_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["download_menu"] = "source"

    await update.message.reply_text(
        "DOWNLOAD\n\n"
        "1. Hugging Face\n"
        "2. CivitAI\n"
        "0. Отмена"
    )


MODEL_TYPES = {
    "1": ("Checkpoints", "models/checkpoints"),
    "2": ("LoRAs", "models/loras"),
    "3": ("Text Encoders", "models/text_encoders"),
    "4": ("VAE", "models/vae"),
    "5": ("Diffusion Models", "models/diffusion_models"),
    "6": ("Upscalers", "models/upscale_models"),
    "7": ("CLIP", "models/clip"),
    "8": ("Other", "models"),
}


async def download_type_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("download_menu") != "type":
        return

    choice = update.message.text.strip()

    if choice == "0":
        context.user_data["download_menu"] = "source"
        await update.message.reply_text(
            "DOWNLOAD\n\n"
            "1. Hugging Face\n"
            "2. CivitAI\n"
            "0. Отмена"
        )
        return

    if choice not in MODEL_TYPES:
        await update.message.reply_text("Выбери номер от 1 до 8 или 0.")
        return

    name, folder = MODEL_TYPES[choice]

    context.user_data["download_type"] = name
    context.user_data["download_folder"] = folder
    context.user_data["download_menu"] = "url"

    await update.message.reply_text(
        f"Тип: {name}\n"
        f"Папка: {folder}\n\n"
        "Отправь URL модели."
    )


async def download_url_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("download_menu") != "url":
        return

    url = update.message.text.strip()

    if not (url.startswith("https://") or url.startswith("http://")):
        await update.message.reply_text("Нужен корректный URL, начинающийся с http:// или https://")
        return

    folder = context.user_data.get("download_folder", "models")
    source = context.user_data.get("download_source", "unknown")

    context.user_data["download_url"] = url
    context.user_data["download_menu"] = None

    progress_message = await update.message.reply_text(
        f"Источник: {source}\n"
        f"Папка: /workspace/ComfyUI/{folder}\n\n"
        "Начинаю скачивание..."
    )

    last_update = {"percent": -1}

    async def progress_callback(downloaded, total):
        percent = int(downloaded * 100 / total)

        if percent == last_update["percent"]:
            return

        if percent % 5 != 0 and percent != 100:
            return

        last_update["percent"] = percent

        downloaded_gb = downloaded / 1024 / 1024 / 1024
        total_gb = total / 1024 / 1024 / 1024

        try:
            await progress_message.edit_text(
                f"Источник: {source}\n"
                f"Папка: /workspace/ComfyUI/{folder}\n\n"
                f"Скачивание: {downloaded_gb:.2f} / {total_gb:.2f} GB "
                f"({percent}%)"
            )
        except Exception:
            pass

    return_code, output = await download_file(
        url,
        folder,
        progress_callback=progress_callback,
    )

    if return_code == 0:
        await update.message.reply_text(
            "Скачивание завершено."
        )
    else:
        last_lines = "\n".join(output[-5:])
        await update.message.reply_text(
            "Ошибка скачивания.\n\n"
            f"{last_lines}"
        )


async def download_file(url, folder, progress_callback=None):
    target_dir = Path("/workspace/ComfyUI") / folder
    target_dir.mkdir(parents=True, exist_ok=True)

    # Hugging Face
    if "huggingface.co/" in url:
        parts = url.split("huggingface.co/", 1)[1].split("/")

        if len(parts) < 4:
            return 1, ["Некорректный Hugging Face URL"]

        repo_id = "/".join(parts[:2])

        try:
            marker = parts.index("resolve")
            filename = "/".join(parts[marker + 2:])
        except (ValueError, IndexError):
            return 1, ["Не удалось определить имя файла из Hugging Face URL"]

        hf_token = load_hf_token()

        if not hf_token:
            return 1, ["Hugging Face Token не найден"]

        try:
            path = hf_hub_download(
                repo_id=repo_id,
                filename=filename,
                token=hf_token,
                local_dir=str(target_dir),
            )

            return 0, [f"СКАЧАНО: {path}"]

        except Exception as e:
            return 1, [f"{type(e).__name__}: {e}"]

    # CivitAI
    if "civitai.com/api/download/models/" in url:
        token = load_civitai_token()

        if not token:
            return 1, ["CivitAI Token не найден"]

        try:
            response = requests.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                stream=True,
                timeout=60,
                allow_redirects=True,
            )

            response.raise_for_status()

            filename = None
            content_disposition = response.headers.get("Content-Disposition", "")

            if "filename=" in content_disposition:
                filename = content_disposition.split("filename=", 1)[1].strip().strip('"')

            if not filename:
                filename = url.rstrip("/").split("/")[-1] or "model.safetensors"

            output_path = target_dir / filename

            total = int(response.headers.get("Content-Length", 0))
            downloaded = 0
            last_percent = -1

            with open(output_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)

                        if total and progress_callback:
                            percent = int(downloaded * 100 / total)

                            if percent != last_percent:
                                last_percent = percent
                                await progress_callback(downloaded, total)

            return 0, [
                f"СКАЧАНО: {output_path}",
                f"Размер: {downloaded / 1024 / 1024:.1f} MB",
            ]

        except Exception as e:
            return 1, [f"{type(e).__name__}: {e}"]

    # Остальные источники пока скачиваем через aria2c
    command = [
        "aria2c",
        "-x", "16",
        "-s", "16",
        "--file-allocation=none",
        "--summary-interval=1",
        "--console-log-level=notice",
        "-d", str(target_dir),
        url,
    ]

    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    output = []

    while True:
        line = await process.stdout.readline()
        if not line:
            break

        decoded = line.decode(errors="ignore").strip()

        if decoded:
            output.append(decoded)

    return_code = await process.wait()

    return return_code, output

MODEL_MANAGER_TYPES = {
    "Checkpoints": "models/checkpoints",
    "LoRAs": "models/loras",
    "Text Encoders": "models/text_encoders",
    "VAE": "models/vae",
    "Diffusion Models": "models/diffusion_models",
    "Upscalers": "models/upscale_models",
    "CLIP": "models/clip",
    "Other": "models",
}


def get_installed_models():
    models = []
    number = 1

    for category, relative_folder in MODEL_MANAGER_TYPES.items():
        folder = Path("/workspace/ComfyUI") / relative_folder

        if not folder.exists():
            continue

        for file_path in sorted(folder.iterdir()):
            if not file_path.is_file():
                continue

            # Не показываем служебные placeholder-файлы ComfyUI
            if file_path.name.startswith("put_") or file_path.name == ".gitkeep":
                continue

            # Для Other не показываем файлы из подпапок других категорий
            if category == "Other":
                if any(
                    str(file_path).startswith(
                        str(Path("/workspace/ComfyUI") / subfolder) + "/"
                    )
                    for subfolder in MODEL_MANAGER_TYPES.values()
                    if subfolder != "models"
                ):
                    continue

            size_gb = file_path.stat().st_size / 1024 / 1024 / 1024

            models.append({
                "number": number,
                "category": category,
                "name": file_path.name,
                "path": str(file_path),
                "size_gb": size_gb,
            })

            number += 1

    return models


async def manager_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    models = get_installed_models()

    if not models:
        await update.message.reply_text(
            "MODEL MANAGER\n\n"
            "Модели не найдены."
        )
        return

    context.user_data["manager_models"] = models
    context.user_data["manager_menu"] = "select"

    lines = ["MODEL MANAGER\n"]

    current_category = None

    for model in models:
        if model["category"] != current_category:
            current_category = model["category"]
            lines.append(f"\n[{current_category}]")

        lines.append(
            f"{model['number']}. {model['name']} "
            f"({model['size_gb']:.2f} GB)"
        )

    lines.append(
        "\nВведи номер модели для удаления."
    )
    lines.append("0. Отмена")

    await update.message.reply_text("\n".join(lines))


async def download_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):

    # MODEL MANAGER: выбор модели
    if context.user_data.get("manager_menu") == "select":
        text = update.message.text.strip()

        if text == "0":
            context.user_data.pop("manager_menu", None)
            context.user_data.pop("manager_models", None)

            await update.message.reply_text("Удаление отменено.")
            return

        try:
            number = int(text)
        except ValueError:
            await update.message.reply_text(
                "Введи номер модели из списка или 0 для отмены."
            )
            return

        models = context.user_data.get("manager_models", [])

        selected = next(
            (model for model in models if model["number"] == number),
            None,
        )

        if not selected:
            await update.message.reply_text(
                "Модели с таким номером нет. Введи номер из списка или 0."
            )
            return

        context.user_data["manager_selected"] = selected
        context.user_data["manager_menu"] = "confirm"

        await update.message.reply_text(
            "Выбрана модель:\n\n"
            f"{selected['name']}\n"
            f"Категория: {selected['category']}\n"
            f"Размер: {selected['size_gb']:.2f} GB\n\n"
            "Удалить модель?\n\n"
            "1. Да, удалить\n"
            "2. Нет, отмена"
        )
        return

    # MODEL MANAGER: подтверждение удаления
    if context.user_data.get("manager_menu") == "confirm":
        choice = update.message.text.strip()

        if choice == "2":
            context.user_data.pop("manager_menu", None)
            context.user_data.pop("manager_models", None)
            context.user_data.pop("manager_selected", None)

            await update.message.reply_text("Удаление отменено.")
            return

        if choice != "1":
            await update.message.reply_text(
                "Выбери 1 для удаления или 2 для отмены."
            )
            return

        selected = context.user_data.get("manager_selected")

        if not selected:
            context.user_data.pop("manager_menu", None)
            await update.message.reply_text(
                "Выбранная модель больше недоступна."
            )
            return

        model_path = Path(selected["path"]).resolve()
        models_root = Path("/workspace/ComfyUI/models").resolve()

        # Дополнительная защита: удаляем только внутри models
        try:
            model_path.relative_to(models_root)
        except ValueError:
            context.user_data.pop("manager_menu", None)
            context.user_data.pop("manager_models", None)
            context.user_data.pop("manager_selected", None)

            await update.message.reply_text(
                "Ошибка безопасности: файл находится вне папки моделей."
            )
            return

        if not model_path.is_file():
            context.user_data.pop("manager_menu", None)
            context.user_data.pop("manager_models", None)
            context.user_data.pop("manager_selected", None)

            await update.message.reply_text(
                "Файл уже не существует."
            )
            return

        size_bytes = model_path.stat().st_size
        size_gb = size_bytes / 1024 / 1024 / 1024

        try:
            model_path.unlink()
        except Exception as e:
            await update.message.reply_text(
                "Ошибка удаления:\n\n"
                f"{type(e).__name__}: {e}"
            )
            return

        context.user_data.pop("manager_menu", None)
        context.user_data.pop("manager_models", None)
        context.user_data.pop("manager_selected", None)

        await update.message.reply_text(
            "Модель удалена.\n\n"
            f"{selected['name']}\n"
            f"Освобождено: {size_gb:.2f} GB"
        )
        return

    if context.user_data.get("download_menu") == "civitai_token":
        token = update.message.text.strip()

        if not token:
            await update.message.reply_text("Токен не может быть пустым.")
            return

        save_civitai_token(token)
        context.user_data["civitai_token"] = token
        context.user_data["download_menu"] = "type"

        await update.message.reply_text(
            "CivitAI Token сохранён локально.\\n\\n"
            "1. Checkpoints\\n"
            "2. LoRAs\\n"
            "3. Text Encoders\\n"
            "4. VAE\\n"
            "5. Diffusion Models\\n"
            "6. Upscalers\\n"
            "7. CLIP\\n"
            "8. Other\\n"
            "0. Назад"
        )
        return

    if context.user_data.get("download_menu") == "hf_token":
        token = update.message.text.strip()

        if not token:
            await update.message.reply_text("Токен не может быть пустым.")
            return

        save_hf_token(token)
        context.user_data["hf_token"] = token
        context.user_data["download_menu"] = "type"

        await update.message.reply_text(
            "Hugging Face Token сохранён локально.\n\n"
            "1. Checkpoints\n"
            "2. LoRAs\n"
            "3. Text Encoders\n"
            "4. VAE\n"
            "5. Diffusion Models\n"
            "6. Upscalers\n"
            "7. CLIP\n"
            "8. Other\n"
            "0. Назад"
        )
        return

    if context.user_data.get("download_menu") == "type":
        await download_type_choice(update, context)
        return

    if context.user_data.get("download_menu") == "url":
        await download_url_choice(update, context)
        return

    if context.user_data.get("download_menu") != "source":
        return

    choice = update.message.text.strip()

    if choice == "1":
        context.user_data["download_source"] = "huggingface"

        if context.user_data.get("hf_token") or load_hf_token():
            context.user_data["download_menu"] = "type"
            await update.message.reply_text(
                "Hugging Face Token уже сохранён.\n\n"
                "1. Checkpoints\n"
                "2. LoRAs\n"
                "3. Text Encoders\n"
                "4. VAE\n"
                "5. Diffusion Models\n"
                "6. Upscalers\n"
                "7. CLIP\n"
                "8. Other\n"
                "0. Назад"
            )
        else:
            context.user_data["download_menu"] = "hf_token"
            await update.message.reply_text(
                "Hugging Face Token не найден.\n\n"
                "Отправь HF Token."
            )

    elif choice == "2":
        context.user_data["download_source"] = "civitai"

        if context.user_data.get("civitai_token") or load_civitai_token():
            context.user_data["download_menu"] = "type"
            await update.message.reply_text(
                "CivitAI Token уже сохранён.\n\n"
                "1. Checkpoints\n"
                "2. LoRAs\n"
                "3. Text Encoders\n"
                "4. VAE\n"
                "5. Diffusion Models\n"
                "6. Upscalers\n"
                "7. CLIP\n"
                "8. Other\n"
                "0. Назад"
            )
        else:
            context.user_data["download_menu"] = "civitai_token"
            await update.message.reply_text(
                "CivitAI выбран.\n\n"
                "Отправь CivitAI API Token."
            )

    elif choice == "0":
        context.user_data.pop("download_menu", None)
        await update.message.reply_text("Загрузка отменена.")

    else:
        await update.message.reply_text("Выбери 1, 2 или 0.")


app.add_handler(CommandHandler("download", download_command))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, download_choice))
app.add_handler(CommandHandler("manager", manager_command))
app.add_handler(MessageHandler(filters.PHOTO, photo_message))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, download_type_choice))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message))

if __name__ == "__main__":
    print("Bot started")
    app.run_polling()
