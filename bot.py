import os
import logging
import requests
from dotenv import load_dotenv

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    MessageReactionHandler,
    CommandHandler,
    filters
)

# -----------------------------------------
# ЗАГРУЖАЕМ ПЕРЕМЕННЫЕ
# -----------------------------------------

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID"))
TRIGGER_EMOJI = os.getenv("TRIGGER_EMOJI", "🙏")

OPENAI_KEY = os.getenv("OPENAI_API_KEY")

JIRA_BASE_URL = os.getenv("JIRA_BASE_URL")
JIRA_EMAIL = os.getenv("JIRA_EMAIL")
JIRA_TOKEN = os.getenv("JIRA_TOKEN")
JIRA_PROJECT_KEY = os.getenv("JIRA_PROJECT_KEY", "DEV")


# -----------------------------------------
# ЛОГИ
# -----------------------------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# хранение истории сообщений (в памяти)
history = []


# -----------------------------------------
# ФУНКЦИИ JIRA
# -----------------------------------------

def create_jira_issue(summary: str, description: str):
    url = f"{JIRA_BASE_URL}/rest/api/3/issue"

    payload = {
        "fields": {
            "project": {"key": JIRA_PROJECT_KEY},
            "summary": summary[:254],
            "description": description,
            "issuetype": {"name": "Task"}
        }
    }

    response = requests.post(
        url,
        json=payload,
        auth=(JIRA_EMAIL, JIRA_TOKEN),
        headers={"Content-Type": "application/json"},
        timeout=20
    )

    if response.status_code >= 300:
        logger.error(f"Ошибка Jira: {response.text}")
        return None

    return response.json().get("key")


# -----------------------------------------
# GPT АНАЛИЗ СООБЩЕНИЙ
# -----------------------------------------

def build_task_text(messages):
    if not OPENAI_KEY:
        text = "\n".join(messages)
        return text[:60], text  # summary, description

    import openai
    openai.api_key = OPENAI_KEY

    prompt = f"""
Сделай задачу для Jira из этих сообщений. Верни JSON:

{{
"title": "...",
"description": "..."
}}

Сообщения:
{chr(10).join(messages)}
"""

    try:
        response = openai.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            max_tokens=300
        )

        import json
        data = json.loads(response.choices[0].message.content)

        return data.get("title", "Task"), data.get("description", "")
    except Exception as e:
        logger.error(f"OpenAI error: {e}")
        text = "\n".join(messages)
        return text[:60], text


# -----------------------------------------
# ОБРАБОТЧИКИ СОБЫТИЙ TELEGRAM
# -----------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Бот работает.")


async def save_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id = msg.chat_id

    if chat_id != TELEGRAM_CHAT_ID:
        return

    # сохраняем последние 100 сообщений
    history.append(msg)
    if len(history) > 100:
        history.pop(0)


async def reaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    react = update.message_reaction
    if react is None:
        return

    if react.chat.id != TELEGRAM_CHAT_ID:
        return

    new_emojis = [r.emoji for r in react.new_reaction or []]
    if TRIGGER_EMOJI not in new_emojis:
        return

    msg_id = react.message_id

    # ищем сообщение по ID
    target = None
    for msg in history:
        if msg.message_id == msg_id:
            target = msg
            break

    if not target:
        await context.bot.send_message(
            TELEGRAM_CHAT_ID,
            "Не нашел сообщение. Бот не видел историю.",
        )
        return

    # берем 3 предыдущих + текущее
    idx = history.index(target)
    msgs = history[max(0, idx - 3): idx + 1]

    texts = []
    for m in msgs:
        if m.text:
            texts.append(m.text)

    summary, description = build_task_text(texts)
    key = create_jira_issue(summary, description)

    if key:
        await context.bot.send_message(
            TELEGRAM_CHAT_ID,
            f"Создана задача: {key}",
            reply_to_message_id=msg_id
        )
    else:
        await context.bot.send_message(
            TELEGRAM_CHAT_ID,
            "Ошибка Jira",
            reply_to_message_id=msg_id
        )


# -----------------------------------------
# СТАРТ БОТА
# -----------------------------------------

def main():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("Нет TELEGRAM_TOKEN в .env")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))

    # ловим все сообщения
    app.add_handler(MessageHandler(filters.ALL, save_message))

    # ловим реакции
    app.add_handler(MessageReactionHandler(reaction))

    app.run_polling(allowed_updates=["message", "message_reaction"])


if __name__ == "__main__":
    main()
