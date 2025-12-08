import os
import logging
import requests
from openai import OpenAI

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
TELEGRAM_CHAT_ID = None
TRIGGER_EMOJI = "👹"  # Чертик

OPENAI_KEY = os.getenv("OPENAI_KEY")
openai_client = OpenAI(api_key=OPENAI_KEY) if OPENAI_KEY else None

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
# GPT АНАЛИЗ
# -----------------------------------------

def analyze_with_gpt(messages):
    """Анализирует контекст через GPT и создает структурированную задачу"""
    
    if not openai_client:
        logger.warning("OpenAI key not set, using fallback")
        return fallback_analysis(messages)
    
    # Формируем контекст
    context = "\n\n".join([f"Сообщение {i+1}: {msg}" for i, msg in enumerate(messages)])
    
    prompt = f"""Проанализируй переписку и создай задачу для Jira.

КОНТЕКСТ ИЗ ЧАТА:
{context}

ТВОЯ ЗАДАЧА:
1. Определи о чем идет речь в последнем отмеченном сообщении
2. Используй предыдущие сообщения как контекст для понимания
3. Сформулируй четкую задачу

ФОРМАТ ОТВЕТА (строго соблюдай):
SUMMARY: [краткое название задачи, 5-10 слов]

DESCRIPTION:
[Подробное описание что нужно сделать]

Контекст:
[релевантная информация из переписки]

Технические детали:
[если есть - ошибки, коды, ссылки]"""

    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Ты менеджер задач. Анализируешь переписку и создаешь структурированные задачи для Jira."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=500
        )
        
        result = response.choices[0].message.content.strip()
        
        # Парсим ответ
        lines = result.split('\n')
        summary = ""
        description = []
        in_description = False
        
        for line in lines:
            if line.startswith("SUMMARY:"):
                summary = line.replace("SUMMARY:", "").strip()
            elif line.startswith("DESCRIPTION:"):
                in_description = True
            elif in_description and line.strip():
                description.append(line)
        
        if not summary:
            summary = lines[0][:60] if lines else "Новая задача"
        
        if not description:
            description = [result]
        
        return summary, "\n".join(description)
        
    except Exception as e:
        logger.error(f"GPT analysis failed: {e}")
        return fallback_analysis(messages)

def fallback_analysis(messages):
    """Простой анализ без GPT"""
    text = "\n".join(messages)
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    
    if not lines:
        return "Новая задача", "Нет описания"
    
    summary = lines[0][:60]
    if len(lines[0]) > 60:
        summary += "..."
    
    description_parts = ["*Контекст из чата:*\n"]
    for i, msg in enumerate(messages, 1):
        description_parts.append(f"{i}. {msg}")
    
    return summary, "\n".join(description_parts)

# -----------------------------------------
# ОБРАБОТЧИКИ TELEGRAM
# -----------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ Бот работает!\n\n"
        f"Поставь {TRIGGER_EMOJI} на сообщение для создания задачи в Jira.\n"
        f"Проект: {JIRA_PROJECT_KEY}"
    )

async def save_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg:
        return
        
    chat_id = msg.chat_id

    if TELEGRAM_CHAT_ID and chat_id != TELEGRAM_CHAT_ID:
        return

    history.append(msg)
    
    if len(history) > 100:
        history.pop(0)
    
    logger.debug(f"Message saved: {msg.message_id} from {chat_id}")

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

    logger.info(f"Reaction {TRIGGER_EMOJI} detected on message {msg_id}")

    # Ищем сообщение
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

    thinking_msg = await context.bot.send_message(
        chat_id,
        "🤖 Анализирую контекст и создаю задачу...",
        reply_to_message_id=msg_id
    )

    # Берем последние 10 сообщений до отмеченного
    idx = history.index(target)
    context_msgs = history[max(0, idx - 9): idx + 1]  # 10 сообщений включая текущее

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

    # Анализируем через GPT
    summary, description = analyze_with_gpt(texts)
    
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
    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN не установлен")

    logger.info("=" * 50)
    logger.info("STARTUP ENVIRONMENT CHECK")
    logger.info("=" * 50)
    logger.info(f"TELEGRAM_TOKEN: {'SET' if TELEGRAM_TOKEN else 'MISSING'}")
    logger.info(f"JIRA_BASE_URL: {JIRA_BASE_URL}")
    logger.info(f"JIRA_EMAIL: {JIRA_EMAIL}")
    logger.info(f"JIRA_TOKEN: {'SET' if JIRA_TOKEN else 'MISSING'}")
    logger.info(f"JIRA_PROJECT_KEY: {JIRA_PROJECT_KEY}")
    logger.info(f"OPENAI_KEY: {'SET' if OPENAI_KEY else 'MISSING'}")
    logger.info("=" * 50)
    
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.ALL, save_message))
    app.add_handler(MessageReactionHandler(reaction))

    logger.info(f"🤖 Бот запущен!")
    logger.info(f"📌 Эмодзи: {TRIGGER_EMOJI}")
    logger.info(f"📁 Проект Jira: {JIRA_PROJECT_KEY}")
    logger.info(f"🔗 {JIRA_BASE_URL}")
    
    app.run_polling(
        allowed_updates=["message", "message_reaction"],
        drop_pending_updates=True
    )

if __name__ == "__main__":
    main()
