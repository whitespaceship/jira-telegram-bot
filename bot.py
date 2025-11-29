import os
import json
import requests
from requests.auth import HTTPBasicAuth
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from datetime import datetime

# Config
TELEGRAM_TOKEN = "7835188720:AAG6GU32WREM24CvwheJxeJz7tDpKcWO2y0"
JIRA_URL = "https://overchat.atlassian.net"
JIRA_EMAIL = "k@overchat.ai"
JIRA_API_TOKEN = "ATATT3xFfGF01hoPH3EGiD3DYzynu9PHtezlK3XvqJQflqVFtzYYQSU97fvPfOowD8RNTux0O3Y3NGY1KXxLjEXULixqWGcrrhp6cSSuSSesX93OLMWhHpRPO_7f19subcYW2wWZRe3qoybqDSKPtWxT0pHQwWT9t6WwM-RcniMQJkysN3K2YUQ=924E1184"
JIRA_PROJECT_KEY = "DEV"
OPENAI_API_KEY = "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"

CONFIG = {
    "emoji": "🙏",
    "labels": ["telegram-bot", "auto-created"]
}

def create_jira_task(title, description):
    """Создает задачу в Jira через REST API"""
    url = f"{JIRA_URL}/rest/api/2/issue"
    
    payload = {
        "fields": {
            "project": {"key": JIRA_PROJECT_KEY},
            "summary": title[:250],
            "description": description,
            "issuetype": {"name": "Task"},
            "labels": CONFIG["labels"]
        }
    }
    
    response = requests.post(
        url,
        json=payload,
        auth=HTTPBasicAuth(JIRA_EMAIL, JIRA_API_TOKEN),
        headers={"Content-Type": "application/json"}
    )
    
    if response.status_code == 201:
        return response.json()
    else:
        raise Exception(f"Jira error {response.status_code}: {response.text}")

def analyze_with_openai(context_text):
    """Анализирует контекст через OpenAI"""
    import openai
    openai.api_key = OPENAI_API_KEY
    
    prompt = f"""Проанализируй переписку и создай задачу для Jira.

Переписка:
{context_text}

Верни JSON:
{{
  "title": "Краткий заголовок задачи (до 80 символов)",
  "description": "Подробное описание задачи"
}}"""
    
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "Ты помощник для создания задач в Jira."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.3
    )
    
    content = response.choices[0].message.content
    
    # Извлекаем JSON
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0].strip()
    elif "```" in content:
        content = content.split("```")[1].split("```")[0].strip()
    
    return json.loads(content)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    await update.message.reply_text(
        f"✅ Бот активен!\n\n"
        f"Поставь {CONFIG['emoji']} на сообщение → создам задачу в Jira\n"
        f"Проект: {JIRA_PROJECT_KEY}"
    )

async def handle_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка реакций"""
    if not update.message_reaction:
        return
    
    reaction = update.message_reaction
    chat_id = reaction.chat.id
    message_id = reaction.message_id
    
    # Проверяем эмодзи
    new_reactions = [r.emoji for r in reaction.new_reaction if hasattr(r, 'emoji')]
    if CONFIG["emoji"] not in new_reactions:
        return
    
    try:
        user = reaction.user
        username = user.username or user.first_name
        
        # Отправляем статус
        thinking_msg = await context.bot.send_message(
            chat_id=chat_id,
            text="🤔 Анализирую задачу...",
            reply_to_message_id=message_id
        )
        
        # Упрощенный вариант - берем текст из сообщения на которое поставили реакцию
        try:
            original_msg = await context.bot.forward_message(
                chat_id=chat_id,
                from_chat_id=chat_id,
                message_id=message_id
            )
            context_text = original_msg.text or "Задача из Telegram"
            await context.bot.delete_message(chat_id, original_msg.message_id)
        except:
            context_text = "Задача из Telegram"
        
        # Анализируем через OpenAI
        task_data = analyze_with_openai(context_text)
        
        # Формируем описание
        description = f"""{task_data['description']}

---
Создано из Telegram
Инициатор: {username}
Дата: {datetime.now().strftime('%Y-%m-%d %H:%M')}
Контекст: {context_text[:500]}
"""
        
        # Создаем задачу
        issue = create_jira_task(task_data['title'], description)
        issue_key = issue['key']
        
        # Обновляем сообщение
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=thinking_msg.message_id,
            text=f"✅ Задача создана!\n\n"
                 f"{task_data['title']}\n\n"
                 f"🔗 {JIRA_URL}/browse/{issue_key}",
            disable_web_page_preview=True
        )
        
    except Exception as e:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"❌ Ошибка: {str(e)}",
            reply_to_message_id=message_id
        )

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    
    # Обработчик реакций
    from telegram.ext import MessageReactionHandler
    app.add_handler(MessageReactionHandler(handle_reaction))
    
    print(f"🤖 Бот запущен. Эмодзи: {CONFIG['emoji']}, Проект: {JIRA_PROJECT_KEY}")
    app.run_polling(allowed_updates=["message", "message_reaction"])

if __name__ == "__main__":
    main()
