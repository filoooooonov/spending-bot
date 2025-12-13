from typing import Final
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv
from datetime import datetime
import os
import json
import re

load_dotenv()

TOKEN: Final = os.environ.get('TELEGRAM_BOT_TOKEN')
BOT_USERNAME: Final = os.environ.get('TELEGRAM_BOT_USERNAME', '@AlekseiFilonovSpendingBot')
ALLOWED_USER_ID: Final = os.environ.get('ALLOWED_USER_ID')
SPENDING_DATA_FILE: Final = 'spending_data.json'
INCOME_DATA_FILE: Final = 'income_data.json'


def is_authorized(user_id: int) -> bool:
    """Check if user is authorized to use the bot."""
    if not ALLOWED_USER_ID:
        return True  # No restriction if not set
    return str(user_id) == ALLOWED_USER_ID


def load_spending_data() -> dict:
    """Load spending data from JSON file."""
    if os.path.exists(SPENDING_DATA_FILE):
        with open(SPENDING_DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_spending_data(data: dict) -> None:
    """Save spending data to JSON file."""
    with open(SPENDING_DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_income_data() -> dict:
    """Load income data from JSON file."""
    if os.path.exists(INCOME_DATA_FILE):
        with open(INCOME_DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_income_data(data: dict) -> None:
    """Save income data to JSON file."""
    with open(INCOME_DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# -------------------


def add_expense(user_id: str, amount: float, description: str) -> dict:
    """Add an expense for a user."""
    data = load_spending_data()
    
    if user_id not in data:
        data[user_id] = []
    
    expense = {
        'amount': amount,
        'description': description,
        'date': datetime.now().isoformat()
    }
    
    data[user_id].append(expense)
    save_spending_data(data)
    return expense


def parse_expense(text: str) -> tuple[float, str] | None:
    """Parse expense from text like '15 alepa' or '15.50 grocery store'."""
    match = re.match(r'^(\d+(?:[.,]\d+)?)\s+(.+)$', text.strip())
    if match:
        amount = float(match.group(1).replace(',', '.'))
        description = match.group(2).strip()
        return (amount, description)
    return None

def add_income(user_id: str, amount: float, description: str) -> dict:
    """Add an income for a user."""
    data = load_income_data()
    
    if user_id not in data:
        data[user_id] = []

    income = {
        'amount': amount,
        'description': description,
        'date': datetime.now().isoformat()
    }
    
    data[user_id].append(income)
    save_income_data(data)
    return income


def parse_income(text: str) -> tuple[float, str] | None:
    """Parse income from text like '+500 aalto' or '+15.50 salary'."""
    match = re.match(r'^\+(\d+(?:[.,]\d+)?)\s+(.+)$', text.strip())
    if match:
        amount = float(match.group(1).replace(',', '.'))
        description = match.group(2).strip()
        return (amount, description)
    return None


# -------------------


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    await update.message.reply_text(
        '👋 Hello! I\'m your spending tracker bot.\n\n'
        '💰 To log an expense, send: <amount> <description>\n'
        'Example: 15 alepa\n\n'
        '💵 To log income, send: +<amount> <description>\n'
        'Example: +500 aalto\n\n'
        '📊 Commands:\n'
        '/history - View your spending history\n'
        '/total - See your total spending\n'
        '/help - Show this help message'
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    await update.message.reply_text(
        '📖 How to use this bot:\n\n'
        '💰 Log expense: Send a number followed by description\n'
        'Examples:\n'
        '  • 15 alepa\n'
        '  • 25.50 lunch\n'
        '  • 100 groceries\n\n'
        '💵 Log income: Send + followed by amount and description\n'
        'Examples:\n'
        '  • +500 aalto\n'
        '  • +1000 salary\n\n'
        '📊 Commands:\n'
        '/history - View recent expenses\n'
        '/total - See total spending\n'
        '/clear - Clear all your data'
    )


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show spending history for the user."""
    if not is_authorized(update.effective_user.id):
        return
    user_id = str(update.message.chat.id)
    data = load_spending_data()
    
    if user_id not in data or not data[user_id]:
        await update.message.reply_text('📭 No spending history yet. Send an expense like "15 alepa" to start!')
        return
    
    expenses = data[user_id][-10:]  # Last 10 expenses
    
    message = '📜 Your recent expenses:\n\n'
    for exp in reversed(expenses):
        date = datetime.fromisoformat(exp['date']).strftime('%d/%m')
        message += f"• €{exp['amount']:.2f} - {exp['description']} ({date})\n"
    
    await update.message.reply_text(message)


async def total_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show total spending for the user."""
    if not is_authorized(update.effective_user.id):
        return
    user_id = str(update.message.chat.id)
    data = load_spending_data()
    
    if user_id not in data or not data[user_id]:
        await update.message.reply_text('📭 No spending yet!')
        return
    
    total = sum(exp['amount'] for exp in data[user_id])
    count = len(data[user_id])
    
    await update.message.reply_text(f'💰 Total spending: €{total:.2f}\n📝 Total entries: {count}')


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear all spending data for the user."""
    if not is_authorized(update.effective_user.id):
        return
    user_id = str(update.message.chat.id)
    data = load_spending_data()
    
    if user_id in data:
        del data[user_id]
        save_spending_data(data)
    
    await update.message.reply_text('🗑️ All your spending data has been cleared.')


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    
    text: str = update.message.text
    user_id = str(update.message.chat.id)
    
    print(f'User ({user_id}): "{text}"')
    
    # Try to parse as income first (starts with +)
    income = parse_income(text)
    if income:
        amount, description = income
        add_income(user_id, amount, description)
        response = f'💵 Income saved: €{amount:.2f} - {description}'
    else:
        # Try to parse as expense
        expense = parse_expense(text)
        if expense:
            amount, description = expense
            add_expense(user_id, amount, description)
            response = f'✅ Saved: €{amount:.2f} - {description}'
        else:
            response = (
                '❓ I didn\'t understand that.\n\n'
                'To log an expense, send: <amount> <description>\n'
                'Example: 15 alepa\n\n'
                'To log income, send: +<amount> <description>\n'
                'Example: +500 aalto\n\n'
                'Type /help for more info.'
            )
    
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
    app.add_handler(CommandHandler('history', history_command))
    app.add_handler(CommandHandler('total', total_command))
    app.add_handler(CommandHandler('clear', clear_command))

    # Messages
    app.add_handler(MessageHandler(filters.TEXT, handle_message))

    # Errors
    app.add_error_handler(error)

    # Polls the bot
    print('Polling...')
    app.run_polling(poll_interval=3)    
