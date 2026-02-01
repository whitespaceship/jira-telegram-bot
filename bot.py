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

LINEAR_API_KEY = os.getenv("LINEAR_API_KEY")
LINEAR_TEAM_ID = os.getenv("LINEAR_TEAM_ID")
LINEAR_ASSIGNEE_ID = os.getenv("LINEAR_ASSIGNEE_ID", "08f50554-ec80-4777-b5ff-fe66db110b19")  # egainulina
LINEAR_API_URL = "https://api.linear.app/graphql"

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
# ФУНКЦИИ LINEAR
# -----------------------------------------

def create_linear_issue(title: str, description: str):
    """Создает задачу в Linear через GraphQL API"""

    logger.info(f"=== LINEAR REQUEST DEBUG ===")
    logger.info(f"Creating task: {title}")

    query = """
    mutation IssueCreate($title: String!, $description: String, $teamId: String!, $assigneeId: String) {
        issueCreate(input: {
            title: $title
            description: $description
            teamId: $teamId
            assigneeId: $assigneeId
        }) {
            success
            issue {
                id
                identifier
                url
            }
        }
    }
    """

    variables = {
        "title": title[:200],
        "description": description,
        "teamId": LINEAR_TEAM_ID,
        "assigneeId": LINEAR_ASSIGNEE_ID
    }

    try:
        response = requests.post(
            LINEAR_API_URL,
            json={"query": query, "variables": variables},
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {LINEAR_API_KEY}"
            },
            timeout=20
        )

        if response.status_code >= 300:
            logger.error(f"Linear API error [{response.status_code}]: {response.text}")
            return None, None

        data = response.json()

        if "errors" in data:
            logger.error(f"Linear GraphQL error: {data['errors']}")
            return None, None

        issue_data = data.get("data", {}).get("issueCreate", {})

        if not issue_data.get("success"):
            logger.error(f"Linear issue creation failed: {data}")
            return None, None

        issue = issue_data.get("issue", {})
        identifier = issue.get("identifier")
        url = issue.get("url")

        logger.info(f"Linear task created: {identifier}")
        return identifier, url

    except Exception as e:
        logger.error(f"Linear request failed: {e}")
        return None, None

# -----------------------------------------
# GPT АНАЛИЗ
# -----------------------------------------

def analyze_with_gpt(message_text: str):
    """Анализирует одно сообщение через GPT и создает задачу для Linear"""

    if not openai_client:
        logger.error("OpenAI client not initialized - check OPENAI_KEY")
        return message_text.split('\n')[0][:60], message_text

    prompt = f"""Проанализируй это сообщение из Telegram и создай задачу для Linear.

СООБЩЕНИЕ:
{message_text}

ИНСТРУКЦИЯ:
1. Это ОДНО сообщение — основа для одной задачи
2. Выпиши ВСЕ пункты, упомянутые в сообщении
3. Если в сообщении список — включи все пункты в задачу
4. Если упомянуты API, платформы, инструменты — перечисли их

ФОРМАТ ОТВЕТА (строго):
SUMMARY: [Краткое название охватывающее все пункты, 5-12 слов]

DESCRIPTION:
**Что нужно сделать:**
[Полный список всех пунктов из сообщения]

**Список упомянутого:**
[Все API, платформы, инструменты]"""

    try:
        logger.info("Sending to GPT...")
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Ты senior product manager. Создаешь детальные задачи для Linear на основе одного сообщения. Не добавляй предположений, работай только с тем, что написано в сообщении."},
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
        return message_text.split('\n')[0][:60], message_text

# -----------------------------------------
# ОБРАБОТЧИКИ TELEGRAM
# -----------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ Бот работает!\n\n"
        f"Поставь {TRIGGER_EMOJI} на сообщение для создания задачи в Linear.\n"
        f"Linear: {'✅' if LINEAR_API_KEY else '❌'}\n"
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

    # Берем текст ТОЛЬКО из целевого сообщения
    text = target.text or target.caption or ""

    logger.info(f"Analyzing message: {text[:100]}...")

    if not text:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=thinking_msg.message_id,
            text="❌ Нет текста для задачи"
        )
        return

    # GPT анализ
    summary, description = analyze_with_gpt(text)
    
    await context.bot.edit_message_text(
        chat_id=chat_id,
        message_id=thinking_msg.message_id,
        text="📝 Создаю в Linear..."
    )

    # Linear
    identifier, url = create_linear_issue(summary, description)

    if identifier:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=thinking_msg.message_id,
            text=f"✅ Задача создана!\n\n"
                 f"🔗 {url}\n\n"
                 f"📝 {identifier}: {summary}"
        )
    else:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=thinking_msg.message_id,
            text="❌ Ошибка Linear. Проверь логи."
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
    logger.info(f"LINEAR_API_KEY: {'SET' if LINEAR_API_KEY else 'MISSING'}")
    logger.info(f"LINEAR_TEAM_ID: {LINEAR_TEAM_ID}")
    logger.info(f"OPENAI_KEY: {'SET' if OPENAI_KEY else 'MISSING'}")
    logger.info(f"OpenAI Client: {'OK' if openai_client else 'NOT INITIALIZED'}")
    logger.info("=" * 50)
    
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.ALL, save_message))
    app.add_handler(MessageReactionHandler(reaction))

    logger.info(f"🤖 Бот запущен!")
    logger.info(f"📌 Эмодзи: {TRIGGER_EMOJI}")
    logger.info(f"📁 Linear Team ID: {LINEAR_TEAM_ID}")
    
    app.run_polling(
        allowed_updates=["message", "message_reaction"],
        drop_pending_updates=True
    )

if __name__ == "__main__":
    main()
