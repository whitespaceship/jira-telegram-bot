import os
import logging
import requests

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
# КОНФИГУРАЦИЯ
# -----------------------------------------

TELEGRAM_TOKEN = "7835188720:AAG6GU32WREM24CvwheJxeJz7tDpKcWO2y0"
TELEGRAM_CHAT_ID = None  # None = работает во всех чатах где бот админ
TRIGGER_EMOJI = "🙏"

OPENAI_KEY = ""  # Отключено временно

JIRA_BASE_URL = "https://overchat.atlassian.net"
JIRA_EMAIL = "k@overchat.ai"
JIRA_TOKEN = "ATATT3xFfGF0eq0eoZgpRB98BeWSCckMmtc8YmHHNIa6lDIEFvGA570Benz5VS7vPUPBTx2NtnxnkatlwG-eEKVl0qBpoPqapXmSsZngh1g6bTeS1t3phiQix0ESwg_Dpco1GW7D6vSpWdKNAhrKqXDgKdmVYVUg9cnZS5JgumuM86atj0Nyqns=1EE27398"
JIRA_PROJECT_KEY = "DEV"

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
    url = f"{JIRA_BASE_URL}/rest/api/2/issue"

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
    # OpenAI отключен - просто берем текст
    text = "\n".join(messages)
    return text[:60], text

# -----------------------------------------
# ОБРАБОТЧИКИ СОБЫТИЙ TELEGRAM
# -----------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Бот работает! Поставь 🙏 на сообщение для создания задачи.")

async def save_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id = msg.chat_id

    if TELEGRAM_CHAT_ID and chat_id != TELEGRAM_CHAT_ID:
        return

    history.append(msg)
    if len(history) > 100:
        history.pop(0)

async def reaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    react = update.message_reaction
    if react is None:
        return

    if TELEGRAM_CHAT_ID and react.chat.id != TELEGRAM_CHAT_ID:
        return

    new_emojis = [r.emoji for r in react.new_reaction or []]
    if TRIGGER_EMOJI not in new_emojis:
        return

    msg_id = react.message_id
    chat_id = react.chat.id

    # ищем сообщение по ID
    target = None
    for msg in history:
        if msg.message_id == msg_id:
            target = msg
            break

    if not target:
        await context.bot.send_message(
            chat_id,
            "❌ Не нашел сообщение. Бот не видел историю.",
        )
        return

    thinking_msg = await context.bot.send_message(
        chat_id,
        "🤔 Создаю задачу...",
        reply_to_message_id=msg_id
    )

    # берем 3 предыдущих + текущее
    idx = history.index(target)
    msgs = history[max(0, idx - 3): idx + 1]

    texts = []
    for m in msgs:
        if m.text:
            texts.append(m.text)

    if not texts:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=thinking_msg.message_id,
            text="❌ Нет текста для анализа"
        )
        return

    summary, description = build_task_text(texts)
    key = create_jira_issue(summary, description)

    if key:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=thinking_msg.message_id,
            text=f"✅ Задача создана!\n\n🔗 {JIRA_BASE_URL}/browse/{key}"
        )
    else:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=thinking_msg.message_id,
            text="❌ Ошибка при создании в Jira. Проверь логи Railway."
        )

# -----------------------------------------
# СТАРТ БОТА
# -----------------------------------------

def main():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("Нет TELEGRAM_TOKEN")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.ALL, save_message))
    app.add_handler(MessageReactionHandler(reaction))

    logger.info(f"🤖 Бот запущен! Эмодзи: {TRIGGER_EMOJI}, Проект: {JIRA_PROJECT_KEY}")
    app.run_polling(allowed_updates=["message", "message_reaction"])

if __name__ == "__main__":
    main()
