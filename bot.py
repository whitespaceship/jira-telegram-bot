import os
import logging
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
import anthropic

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Initialize Anthropic client
client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

async def handle_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle emoji reactions on messages.
    Analyzes only the message that received the emoji reaction.
    """
    if update.message_reaction is None:
        return
    
    # Get the message that received the reaction
    chat_id = update.message_reaction.chat_id
    message_id = update.message_reaction.message_id
    
    try:
        # Fetch the specific message that was reacted to
        message = await context.bot.get_messages(chat_id, message_ids=[message_id])
        
        if not message or not message[0].text:
            logger.warning(f"Could not retrieve message {message_id} or message has no text")
            return
        
        reacted_message = message[0]
        message_text = reacted_message.text
        
        logger.info(f"Processing reaction on message: {message_text[:50]}...")
        
        # Analyze only the reacted message using Claude
        analysis = await analyze_message_with_claude(message_text)
        
        if analysis:
            # Send the analysis back to the chat
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"📊 Analysis of reacted message:\n\n{analysis}",
                reply_to_message_id=message_id
            )
    
    except Exception as e:
        logger.error(f"Error handling reaction: {e}")
        await context.bot.send_message(
            chat_id=update.message_reaction.chat_id,
            text=f"❌ Error processing reaction: {str(e)}"
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle text messages in the chat.
    Only processes messages if they are explicitly sent with a command.
    """
    if update.message is None or not update.message.text:
        return
    
    message_text = update.message.text
    
    # Only respond to messages that start with a command (e.g., /analyze)
    if not message_text.startswith('/'):
        return
    
    try:
        # Process the command
        if message_text.startswith('/analyze'):
            # Analyze the current message
            analysis = await analyze_message_with_claude(message_text)
            
            if analysis:
                await update.message.reply_text(
                    f"📊 Analysis:\n\n{analysis}"
                )
    
    except Exception as e:
        logger.error(f"Error handling message: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")

async def analyze_message_with_claude(message_text: str) -> str:
    """
    Analyze a single message using Claude AI.
    Works with only the provided message, no context from other messages.
    """
    try:
        response = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": f"Please analyze the following message and provide insights:\n\n{message_text}"
                }
            ]
        )
        
        return response.content[0].text
    
    except Exception as e:
        logger.error(f"Error analyzing message with Claude: {e}")
        return None

def main() -> None:
    """Start the bot."""
    # Get the Telegram bot token from environment
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN environment variable not set")
        return
    
    # Create the Application
    application = Application.builder().token(token).build()
    
    # Add handlers
    # Handler for emoji reactions on messages
    application.add_handler(
        MessageHandler(
            filters.StatusUpdate.MESSAGE_REACTION,
            handle_reaction
        )
    )
    
    # Handler for text messages
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message
        )
    )
    
    # Handler for commands
    application.add_handler(
        MessageHandler(
            filters.COMMAND,
            handle_message
        )
    )
    
    logger.info("Bot started. Waiting for messages and reactions...")
    
    # Start the bot
    application.run_polling()

if __name__ == '__main__':
    main()
