import os
import json
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes, CommandHandler
from jira import JIRA
import openai
from datetime import datetime

# Config
TELEGRAM_TOKEN = "7835188720:AAG6GU32WREM24CvwheJxeJz7tDpKcWO2y0"
JIRA_URL = "https://overchat.atlassian.net"
JIRA_EMAIL = "k@overchat.ai"
JIRA_API_TOKEN = "O7BuudbDG1iVFWBDaZmW"
JIRA_PROJECT_KEY = "DEV"
OPENAI_API_KEY = "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"

# Jira & OpenAI
jira = JIRA(server=JIRA_URL, basic_auth=(JIRA_EMAIL, JIRA_API_TOKEN))
openai.api_key = OPENAI_API_KEY

# Настройки
CONFIG = {
    "emoji": "🙏",
    "context_messages": 15,
    "default_assignee": None,
    "default_priority": "Medium",
    "labels": ["telegram-bot", "auto-created"]
}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    await update.message.reply_text(
        f"✅ Бот активен!\n\n"
        f"Поставь {CONFIG['emoji']} на сообщение → создам задачу в Jira\n"
        f"Проект: {JIRA_PROJECT_KEY}\n"
        f"Анализирую последние {CONFIG['context_messages']} сообщений"
    )

async def set_emoji(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /setemoji для смены эмодзи"""
    if not context.args:
        await update.message.reply_text(f"Текущий эмодзи: {CONFIG['emoji']}\nИспользуй: /setemoji 🔥")
        return
    
    CONFIG["emoji"] = context.args[0]
    await update.message.reply_text(f"✅ Эмодзи изменен на {CONFIG['emoji']}")

async def handle_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка реакций на сообщения"""
    
    if not update.message_reaction:
        return
    
    reaction = update.message_reaction
    chat_id = reaction.chat.id
    message_id = reaction.message_id
    
    # Проверяем нужную реакцию
    new_reactions = [r.emoji for r in reaction.new_reaction if hasattr(r, 'emoji')]
    if CONFIG["emoji"] not in new_reactions:
        return
    
    try:
        # Собираем контекст - упрощенная версия
        messages = []
        
        # Получаем информацию о реагировавшем пользователе
        user = reaction.user
        username = user.username or user.first_name
        
        # Отправляем "думаю..."
        thinking_msg = await context.bot.send_message(
            chat_id=chat_id,
            text="🤔 Анализирую задачу...",
            reply_to_message_id=message_id
        )
        
        # Простой промпт для создания задачи
        prompt = """Создай задачу для Jira на основе того, что пользователь отметил реакцией.

Верни JSON:
{
  "title": "Краткий заголовок задачи",
  "description": "Описание задачи"
}"""

        response = openai.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "Ты помощник для создания задач в Jira."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3
        )
        
        # Парсим ответ
        content = response.choices[0].message.content
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()
        
        task_data = json.loads(content)
        
        # Формируем описание
        description = f"""{task_data['description']}

---
Создано из Telegram
Инициатор: {username}
Дата: {datetime.now().strftime('%Y-%m-%d %H:%M')}
"""
        
        # Создаем задачу в Jira
        issue_dict = {
            'project': JIRA_PROJECT_KEY,
            'summary': task_data['title'][:250],
            'description': description,
            'issuetype': {'name': 'Task'},
            'labels': CONFIG['labels']
        }
        
        issue = jira.create_issue(fields=issue_dict)
        
        # Обновляем сообщение
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=thinking_msg.message_id,
            text=f"✅ Задача создана!\n\n"
                 f"{task_data['title']}\n\n"
                 f"🔗 {JIRA_URL}/browse/{issue.key}",
            disable_web_page_preview=True
        )
        
    except json.JSONDecodeError as e:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"❌ Ошибка парсинга",
            reply_to_message_id=message_id
        )
    except Exception as e:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"❌ Ошибка: {str(e)}",
            reply_to_message_id=message_id
        )

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статистика созданных задач"""
    try:
        jql = f'project = {JIRA_PROJECT_KEY} AND labels = "telegram-bot" AND created >= -30d ORDER BY created DESC'
        issues = jira.search_issues(jql, maxResults=50)
        
        stats_text = f"📊 Статистика за 30 дней\n\n"
        stats_text += f"Создано задач: {len(issues)}\n\n"
        
        if issues:
            stats_text += "Последние 5 задач:\n"
            for issue in issues[:5]:
                stats_text += f"• {issue.key} - {issue.fields.summary[:50]}...\n"
        
        await update.message.reply_text(stats_text)
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setemoji", set_emoji))
    app.add_handler(CommandHandler("stats", stats))
    
    # Обработчик реакций
    from telegram.ext import MessageReactionHandler
    app.add_handler(MessageReactionHandler(handle_reaction))
    
    print(f"🤖 Бот запущен. Эмодзи: {CONFIG['emoji']}, Проект: {JIRA_PROJECT_KEY}")
    app.run_polling(allowed_updates=["message", "message_reaction"])

if __name__ == "__main__":
    main()
