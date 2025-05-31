import os
import json
import google.generativeai as genai
import schedule
import threading
import time
import asyncio
import logging
from datetime import datetime, timedelta
from telegram import Update, Bot
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

# === Логирование ===
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# === 🔐 Ключи из переменных окружения ===
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    raise RuntimeError("Не заданы переменные окружения TELEGRAM_TOKEN или GEMINI_API_KEY")

# === Инициализация Gemini ===
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.0-flash")

# === Файл данных ===
DATA_FILE = "data.json"

def load_data():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# === Генерация текста Gemini ===
async def generate_message(prompt):
    try:
        response = await asyncio.to_thread(model.generate_content, prompt)
        return response.text.strip()
    except Exception as e:
        logging.error(f"Ошибка Gemini: {e}")
        return "Сейчас не могу ответить."

# === Отправка напоминания с задержкой ===
async def delayed_send_reminder(chat_id, task, delay):
    await asyncio.sleep(delay)
    try:
        await bot.send_message(chat_id=chat_id, text=f"⏰ Через 10 минут: {task}")
    except Exception as e:
        logging.error(f"Ошибка при отправке напоминания: {e}")

# === Планирование задачи ===
def schedule_task(chat_id, time_str, task):
    try:
        task_time = datetime.strptime(time_str, "%H:%M") - timedelta(minutes=10)
        now = datetime.now()
        scheduled_time = now.replace(hour=task_time.hour, minute=task_time.minute, second=0, microsecond=0)
        if scheduled_time < now:
            scheduled_time += timedelta(days=1)

        delay = (scheduled_time - now).total_seconds()
        asyncio.run_coroutine_threadsafe(delayed_send_reminder(chat_id, task, delay), loop)
    except Exception as e:
        logging.error(f"Ошибка планирования задачи: {e}")

# === Функция для отправки сообщений с учётом лимита Telegram 4096 символов ===
async def send_long_message(chat_id, text):
    MAX_LEN = 4096
    for i in range(0, len(text), MAX_LEN):
        await bot.send_message(chat_id=chat_id, text=text[i:i+MAX_LEN])

# === Утро ===
async def morning_routine():
    data = load_data()
    for chat_id in data:
        motivation = await generate_message("Напиши короткое утреннее вдохновляющее сообщение для школьника.")
        await send_long_message(chat_id, f"☀️ Доброе утро!\n\n{motivation}\n\nЧто ты хочешь сделать сегодня?")
        data[chat_id]["mode"] = "plan"
    save_data(data)

# === Вечер ===
async def evening_routine():
    data = load_data()
    for chat_id in data:
        await bot.send_message(chat_id=chat_id, text="🌙 Как прошёл твой день?")
        data[chat_id]["mode"] = "reflect"
    save_data(data)

# === Планировщик времени ===
def run_schedule(loop):
    schedule.every().day.at("16:30").do(lambda: asyncio.run_coroutine_threadsafe(morning_routine(), loop))
    schedule.every().day.at("20:00").do(lambda: asyncio.run_coroutine_threadsafe(evening_routine(), loop))
    while True:
        schedule.run_pending()
        time.sleep(1)

# === Обработка сообщений ===
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    text = update.message.text
    data = load_data()

    if chat_id not in data:
        data[chat_id] = {"diary": [], "mode": None, "tasks": []}

    mode = data[chat_id].get("mode")

    if mode == "plan":
        data[chat_id]["diary"].append({"type": "планы", "text": text})
        ai_reply = await generate_message(f"Вот план школьника: {text}\nДай советы и добавь недостающие пункты.")
        await send_long_message(chat_id, f"✅ План записан и дополнен ИИ:\n\n{ai_reply}\n\nТеперь напиши каждое задание с указанием времени. Например:\n\n8:00 Математика\n9:30 Прогулка\n...")
        data[chat_id]["mode"] = "schedule"

    elif mode == "schedule":
        lines = text.strip().split("\n")
        tasks = []
        for line in lines:
            try:
                time_part, task_part = line.split(" ", 1)
                datetime.strptime(time_part, "%H:%M")  # Проверка формата времени
                tasks.append((time_part, task_part))
                schedule_task(chat_id, time_part, task_part)
            except ValueError:
                await bot.send_message(chat_id=chat_id, text=f"⚠️ Неверный формат строки: '{line}'. Используй формат 'HH:MM Задание'")
                return
        data[chat_id]["tasks"] = tasks
        data[chat_id]["mode"] = None
        await bot.send_message(chat_id=chat_id, text="✅ Задания с временем записаны. Буду напоминать за 10 минут до начала!")

    elif mode == "reflect":
        data[chat_id]["diary"].append({"type": "рефлексия", "text": text})
        reply = await generate_message(f"Вот дневниковая запись школьника: '{text}'. Ответь поддержкой или советом.")
        await send_long_message(chat_id, f"💬 {reply}")
        data[chat_id]["mode"] = None

    else:
        # Свободный режим общения
        response = await generate_message(f"Пользователь пишет: {text}")
        await send_long_message(chat_id, response)

    save_data(data)

# === /start ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    data = load_data()
    if chat_id not in data:
        data[chat_id] = {"diary": [], "mode": None, "tasks": []}
        save_data(data)
    await bot.send_message(chat_id=chat_id, text="Привет! Я ИИ-дневник 🤖. Утром я спрошу твой план, помогу его улучшить, запишу шаги и буду напоминать о них. Также можешь просто пообщаться со мной.")

# === Запуск ===
bot = Bot(token=TELEGRAM_TOKEN)
app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    threading.Thread(target=run_schedule, args=(loop,), daemon=True).start()

    print("Бот запущен.")
    app.run_polling()
