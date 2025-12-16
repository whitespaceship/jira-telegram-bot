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
TRIGGER_EMOJI = "😈"

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
    logger.info(f"Creating task: {summary}")

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
    """Анализирует контекст через GPT и создает задачу в Jira стиле"""
    
    if not openai_client:
        logger.error("OpenAI client not initialized - check OPENAI_KEY")
        text = "\n".join(messages)
        return text.split('\n')[0][:60], text
    
    context = "\n".join([f"[{i+1}] {msg}" for i, msg in enumerate(messages)])
    
    prompt = f"""Проанализируй переписку из Telegram и создай задачу для Jira.

ПЕРЕПИСКА:
{context}

ИНСТРУКЦИЯ ПО АНАЛИЗУ:
1. ПОСЛЕДНЕЕ сообщение (отмеченное реакцией) — основа задачи
2. В последнем сообщении может быть перечислено НЕСКОЛЬКО пунктов — включи их ВСЕ
3. Прочитай предыдущие сообщения — если они дают полезный контекст для понимания задачи, используй их
4. Если предыдущие сообщения НЕ связаны с последним — игнорируй их

ТВОЯ ЗАДАЧА:
- Выписать ВСЕ упомянутые в последнем сообщении: API, платформы, инструменты, действия
- Добавить релевантный контекст из предыдущих сообщений ТОЛЬКО если он помогает понять задачу
- Создать полную задачу без потери деталей

ФОРМАТ ОТВЕТА (строго):
SUMMARY: [Краткое название охватывающее все пункты, 5-12 слов]

DESCRIPTION:
*Что нужно сделать:*
[Полный список всех пунктов из последнего сообщения с описанием]

*Контекст из переписки:*
[Только релевантная информация которая помогает понять задачу]

*Список упомянутого:*
[Все API, платформы, инструменты из последнего сообщения]

КРИТИЧНО: Если в последнем сообщении список из 10 пунктов — все 10 должны быть в задаче."""

    try:
        logger.info("Sending to GPT...")
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Ты senior product manager. Создаешь детальные задачи для Jira. Твой принцип: не терять ни одной детали из отмеченного сообщения, но использовать контекст из предыдущих сообщений когда он релевантен."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2,
            max_tokens=1200
        )
        
        result = response.choices[0].message.content.strip()
        logger.info(f"GPT response: {result[:150]}...")
        
        # Парсим
        summary = ""
        description = []
        in_description = False
        
        for line in result.split('\n'):
            stripped = line.strip()
            
            if stripped.startswith("SUMMARY:"):
                summary = stripped.replace("SUMMARY:", "").strip()
            elif stripped.startswith("DESCRIPTION:"):
                in_description = True
            elif in_description and stripped:
                description.append(line)
        
        if not summary:
            summary = result.split('\n')[0][:60]
        
        final_description = "\n".join(description) if description else result
        
        logger.info(f"Parsed - Summary: {summary}")
        logger.info(f"Description length: {len(final_description)} chars")
        
        return summary, final_description
        
    except Exception as e:
        logger.error(f"GPT failed: {e}", exc_info=True)
        text = "\n".join(messages)
        return text.split('\n')[0][:60], text

# -----------------------------------------
# ОБРАБОТЧИКИ TELEGRAM
# -----------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ Бот работает!\n\n"
        f"Поставь {TRIGGER_EMOJI} на сообщение для создания задачи в Jira.\n"
        f"Проект: {JIRA_PROJECT_KEY}\n"
        f"OpenAI: {'✅' if openai_client else '❌'}"
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

    logger.info(f"Reaction {TRIGGER_EMOJI} on message {msg_id}")

    target = None
    for msg in history:
        if msg.message_id == msg_id:
            target = msg
            break

    if not target:
        logger.warning(f"Message {msg_id} not found")
        await context.bot.send_message(
            chat_id,
            "❌ Сообщение не найдено в истории бота."
        )
        return

    thinking_msg = await context.bot.send_message(
        chat_id,
        "🤖 Анализирую через GPT...",
        reply_to_message_id=msg_id
    )

    # Берем ТОЛЬКО 7 сообщений ДО отмеченного + само отмеченное
    idx = history.index(target)
    context_msgs = history[max(0, idx - 7): idx + 1]

    texts = []
    for m in context_msgs:
        if m.text:
            texts.append(m.text)

    logger.info(f"Collected {len(texts)} messages for analysis")

    if not texts:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=thinking_msg.message_id,
            text="❌ Нет текста для задачи"
        )
        return

    # GPT анализ
    summary, description = analyze_with_gpt(texts)
    
    await context.bot.edit_message_text(
        chat_id=chat_id,
        message_id=thinking_msg.message_id,
        text="📝 Создаю в Jira..."
    )
    
    # Jira
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
            text="❌ Ошибка Jira. Проверь логи."
        )

# -----------------------------------------
# СТАРТ БОТА
# -----------------------------------------

def main():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN не установлен")

    logger.info("=" * 50)
    logger.info("STARTUP CHECK")
    logger.info("=" * 50)
    logger.info(f"TELEGRAM_TOKEN: {'SET' if TELEGRAM_TOKEN else 'MISSING'}")
    logger.info(f"JIRA_EMAIL: {JIRA_EMAIL}")
    logger.info(f"JIRA_TOKEN: {'SET' if JIRA_TOKEN else 'MISSING'}")
    logger.info(f"JIRA_PROJECT_KEY: {JIRA_PROJECT_KEY}")
    logger.info(f"OPENAI_KEY: {'SET' if OPENAI_KEY else 'MISSING'}")
    logger.info(f"OpenAI Client: {'OK' if openai_client else 'NOT INITIALIZED'}")
    logger.info("=" * 50)
    
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.ALL, save_message))
    app.add_handler(MessageReactionHandler(reaction))

    logger.info(f"🤖 Бот запущен!")
    logger.info(f"📌 Эмодзи: {TRIGGER_EMOJI}")
    logger.info(f"📁 Проект: {JIRA_PROJECT_KEY}")
    
    app.run_polling(
        allowed_updates=["message", "message_reaction"],
        drop_pending_updates=True
    )

if __name__ == "__main__":
    main()
