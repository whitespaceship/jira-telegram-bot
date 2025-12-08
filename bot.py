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

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = None  # None = работает во всех чатах где бот админ
TRIGGER_EMOJI = "😈"  # Голубь мира

OPENAI_KEY = os.getenv("OPENAI_KEY")

JIRA_BASE_URL = os.getenv("JIRA_BASE_URL", "https://overchat.atlassian.net")
JIRA_EMAIL = os.getenv("JIRA_EMAIL")
JIRA_TOKEN = os.getenv("JIRA_TOKEN")
JIRA_PROJECT_KEY = os.getenv("JIRA_PROJECT_KEY", "DEV")

# -----------------------------------------
# ЛОГИ
# -----------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# хранение истории сообщений (в памяти)
history = []

# -----------------------------------------
# ФУНКЦИИ JIRA
# -----------------------------------------

def create_jira_issue(summary: str, description: str):
    """Создает задачу в Jira через REST API v2"""
    url = f"{JIRA_BASE_URL}/rest/api/2/issue"

    logger.info(f"=== JIRA REQUEST DEBUG ===")
    logger.info(f"URL: {url}")
    logger.info(f"Email: {JIRA_EMAIL}")
    logger.info(f"Token starts: {JIRA_TOKEN[:20] if JIRA_TOKEN else 'MISSING'}...")
    logger.info(f"Token ends: ...{JIRA_TOKEN[-10:] if JIRA_TOKEN else 'MISSING'}")
    logger.info(f"Project: {JIRA_PROJECT_KEY}")

    payload = {
        "fields": {
            "project": {"key": JIRA_PROJECT_KEY},
            "summary": summary[:254],
            "description": description,
            "issuetype": {"name": "Task"}
        }
    }

    try:
        response = requests.post(
            url,
            json=payload,
            auth=(JIRA_EMAIL, JIRA_TOKEN),
            headers={"Content-Type": "application/json"},
            timeout=20
        )

        logger.info(f"Response status: {response.status_code}")
        logger.info(f"Response headers: {dict(response.headers)}")
        
        if 'x-seraph-loginreason' in response.headers:
            logger.error(f"CAPTCHA TRIGGERED! x-seraph-loginreason: {response.headers['x-seraph-loginreason']}")

        if response.status_code >= 300:
            logger.error(f"Jira API error [{response.status_code}]: {response.text}")
            return None

        data = response.json()
        logger.info(f"Jira task created: {data.get('key')}")
        return data.get("key")
        
    except Exception as e:
        logger.error(f"Jira request failed: {e}")
        return None

# -----------------------------------------
# АНАЛИЗ И ФОРМАТИРОВАНИЕ ТЕКСТА
# -----------------------------------------

def analyze_and_format(messages):
    """Анализирует контекст и форматирует для Jira"""
    text = "\n".join(messages)
    
    # Определяем summary (первая строка или ключевая фраза)
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    
    if not lines:
        return "Новая задача", "Нет описания"
    
    # Summary = первая строка, макс 60 символов
    summary = lines[0][:60]
    if len(lines[0]) > 60:
        summary += "..."
    
    # Description = структурированное описание
    description_parts = []
    
    # Добавляем все сообщения как контекст
    description_parts.append("*Контекст из чата:*")
    for i, msg in enumerate(messages, 1):
        description_parts.append(f"\n{i}. {msg}")
    
    # Если есть детали, выделяем их
    if len(lines) > 1:
        description_parts.append("\n\n*Детали:*")
        for line in lines[1:]:
            description_parts.append(f"• {line}")
    
    description = "\n".join(description_parts)
    
    return summary, description

# -----------------------------------------
# ОБРАБОТЧИКИ СОБЫТИЙ TELEGRAM
# -----------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    await update.message.reply_text(
        "✅ Бот работает!\n\n"
        f"Поставь {TRIGGER_EMOJI} на сообщение для создания задачи в Jira.\n"
        f"Проект: {JIRA_PROJECT_KEY}"
    )

async def save_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сохраняет все сообщения в историю"""
    msg = update.effective_message
    if not msg:
        return
        
    chat_id = msg.chat_id

    # Фильтр по чату (если задан)
    if TELEGRAM_CHAT_ID and chat_id != TELEGRAM_CHAT_ID:
        return

    history.append(msg)
    
    # Лимит истории
    if len(history) > 100:
        history.pop(0)
    
    logger.debug(f"Message saved: {msg.message_id} from {chat_id}")

async def reaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает реакции на сообщения"""
    react = update.message_reaction
    if react is None:
        return

    # Фильтр по чату
    if TELEGRAM_CHAT_ID and react.chat.id != TELEGRAM_CHAT_ID:
        return

    # Проверяем что добавлен нужный эмодзи
    new_emojis = [r.emoji for r in react.new_reaction or []]
    if TRIGGER_EMOJI not in new_emojis:
        return

    msg_id = react.message_id
    chat_id = react.chat.id

    logger.info(f"Reaction {TRIGGER_EMOJI} detected on message {msg_id}")

    # Ищем сообщение в истории
    target = None
    for msg in history:
        if msg.message_id == msg_id:
            target = msg
            break

    if not target:
        logger.warning(f"Message {msg_id} not found in history")
        await context.bot.send_message(
            chat_id,
            "❌ Сообщение не найдено в истории бота.\n"
            "Бот должен видеть сообщения до реакции."
        )
        return

    # Отправляем "думаем..."
    thinking_msg = await context.bot.send_message(
        chat_id,
        "🕊️ Анализирую и создаю задачу...",
        reply_to_message_id=msg_id
    )

    # Берем контекст: 3 предыдущих + текущее
    idx = history.index(target)
    context_msgs = history[max(0, idx - 3): idx + 1]

    # Извлекаем текст
    texts = []
    for m in context_msgs:
        if m.text:
            texts.append(m.text)

    if not texts:
        logger.warning("No text found in messages")
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=thinking_msg.message_id,
            text="❌ Нет текста для создания задачи"
        )
        return

    # Анализируем и форматируем
    summary, description = analyze_and_format(texts)
    
    # Создаем в Jira
    key = create_jira_issue(summary, description)

    if key:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=thinking_msg.message_id,
            text=f"✅ Задача создана!\n\n"
                 f"🔗 {JIRA_BASE_URL}/browse/{key}\n\n"
                 f"📝 {summary}"
        )
    else:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=thinking_msg.message_id,
            text="❌ Ошибка при создании задачи в Jira.\n"
                 "Проверь логи Railway или права доступа."
        )

# -----------------------------------------
# СТАРТ БОТА
# -----------------------------------------

def main():
    """Запуск бота"""
    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN не установлен")

    logger.info("=" * 50)
    logger.info("STARTUP ENVIRONMENT CHECK")
    logger.info("=" * 50)
    logger.info(f"TELEGRAM_TOKEN: {'SET' if TELEGRAM_TOKEN else 'MISSING'}")
    logger.info(f"JIRA_BASE_URL: {JIRA_BASE_URL}")
    logger.info(f"JIRA_EMAIL: {JIRA_EMAIL}")
    logger.info(f"JIRA_TOKEN: {'SET (' + JIRA_TOKEN[:20] + '...' + JIRA_TOKEN[-10:] + ')' if JIRA_TOKEN else 'MISSING'}")
    logger.info(f"JIRA_PROJECT_KEY: {JIRA_PROJECT_KEY}")
    logger.info(f"OPENAI_KEY: {'SET' if OPENAI_KEY else 'MISSING'}")
    logger.info("=" * 50)
    
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Регистрируем обработчики
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.ALL, save_message))
    app.add_handler(MessageReactionHandler(reaction))

    logger.info(f"🤖 Бот запущен!")
    logger.info(f"📌 Эмодзи: {TRIGGER_EMOJI}")
    logger.info(f"📁 Проект Jira: {JIRA_PROJECT_KEY}")
    logger.info(f"🔗 {JIRA_BASE_URL}")
    
    # Запускаем polling
    app.run_polling(
        allowed_updates=["message", "message_reaction"],
        drop_pending_updates=True
    )

if __name__ == "__main__":
    main()
