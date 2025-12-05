from typing import Final
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv
import os

load_dotenv()

TOKEN: Final = os.environ.get('TELEGRAM_BOT_TOKEN')
BOT_USERNAME: Final = os.environ.get('TELEGRAM_BOT_USERNAME', '@AlekseiFilonovSpendingBot')



async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('Hello! Thank you for using the spending bot. How can I help you today?')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('Help command received! I can help you with your spending. How can I help you today?')

async def custom_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('This is a custom command!')


def handle_response(text: str) -> str:
    processed: str = text.lower()
    if "hello" in processed:
        return "Hello! How can I help you today?"
    elif "help" in processed:
        return "Help command received! I can help you with your spending. How can I help you today?"
    elif "custom" in processed:
        return "This is a custom command!"
    else:
        return "I'm not sure what you mean. Please try again."

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message_type: str = update.message.chat.type
    text: str = update.message.text

    print(f'User ({update.message.chat.id}) in {message_type}: "{text}"')

    response: str = handle_response(text)
    print(f'Bot: {response}')
    await update.message.reply_text(response)

async def error(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print(f'Update {update} caused error {context.error}')

if __name__ == '__main__':
    print('Starting bot...')
    app = Application.builder().token(TOKEN).build()

    # Commands
    app.add_handler(CommandHandler('start', start_command))
    app.add_handler(CommandHandler('help', help_command))
    app.add_handler(CommandHandler('custom', custom_command))

    # Messages
    app.add_handler(MessageHandler(filters.TEXT, handle_message))

    # Errors
    app.add_error_handler(error)

    # Polls the bot
    print('Polling...')
    app.run_polling(poll_interval=3)    
