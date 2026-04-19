from typing import Final
import asyncio
from telegram import Update
from telegram import Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv
import os
import json
import re
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime


load_dotenv()

scopes = [
    "https://www.googleapis.com/auth/spreadsheets"
]

# Load credentials from environment variable (Railway) or file (local)
# According to Railway docs: JSON should be minified (single line) with no external quotes
google_credentials_json = os.environ.get('GOOGLE_CREDENTIALS_JSON')
if google_credentials_json:
    try:
        creds_info = json.loads(google_credentials_json)
        creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse GOOGLE_CREDENTIALS_JSON: {e}")
elif os.path.exists('credentials.json'):
    # Fall back to file for local development
    creds = Credentials.from_service_account_file('credentials.json', scopes=scopes)
else:
    raise ValueError(
        "Google credentials not found. "
        "Set GOOGLE_CREDENTIALS_JSON environment variable (minified JSON, single line) or provide credentials.json file."
    )

client = gspread.authorize(creds)
sheet_id = "14LYEWi4vJi261oTxE1HH4TxluTYcVg4zWDok8IwbJc4"
workbook = client.open_by_key(sheet_id)


TOKEN: Final = os.environ.get('TELEGRAM_BOT_TOKEN')
BOT_USERNAME: Final = os.environ.get('TELEGRAM_BOT_USERNAME', '@AlekseiFilonovSpendingBot')
ALLOWED_USER_ID: Final = os.environ.get('ALLOWED_USER_ID')
SPENDING_DATA_FILE: Final = 'spending_data.json'
TELEGRAM_CURSOR_FILE: Final = os.environ.get("TELEGRAM_CURSOR_FILE", "telegram_cursor.json")


def get_current_sheet() -> gspread.Worksheet:
    """Get the current sheet for the current month."""
    current_month = datetime.now().strftime("%B")
    return workbook.worksheet(current_month)


def load_spending_data() -> dict:
    """Read values from columns M and N starting at row 5, return dict mapping M5: N5."""
    sheet = get_current_sheet()
    
    # Get all values from columns M and N
    col_spending_labels = sheet.col_values(13)  # Column M
    col_spending_amounts = sheet.col_values(14)  # Column N

    
    
    # Slice to get values from row 5 onwards
    spending_labels = col_spending_labels[4:] if len(col_spending_labels) > 4 else []
    spending_amounts = col_spending_amounts[4:] if len(col_spending_amounts) > 4 else []
    
    # Create dictionary, filtering out empty M values
    spending_values: list[dict] = []
    for i in range(max(len(spending_labels), len(spending_amounts))):
        label = spending_labels[i] if i < len(spending_labels) else ""
        amount = spending_amounts[i] if i < len(spending_amounts) else ""
      
        # Only add to dict if label is not empty
        if label.strip():
            spending_values.append({"amount": amount, "label": label})
    
    return spending_values


def is_authorized(user_id: int) -> bool:
    """Check if user is authorized to use the bot."""
    if not ALLOWED_USER_ID:
        return True  # No restriction if not set
    return str(user_id) == ALLOWED_USER_ID


def add_expense(user_id: str, amount: float, label: str) -> bool:
    """Add an expense to the first empty cell starting from row 5 in columns M and N."""
    try:
        sheet = get_current_sheet()
        col_m = sheet.col_values(13)
        
        # Find first empty cell from row 5 onwards
        if len(col_m) < 5:
            next_row = 5
        else:
            empty_index = next(
                (i for i in range(4, len(col_m)) if not col_m[i].strip()),
                len(col_m)
            )
            next_row = empty_index + 1
        
        # Write to columns M, N, and O
        sheet.update(range_name=f"M{next_row}:O{next_row}", values=[[label, amount, datetime.now().strftime("%Y-%m-%d")]])
        
        # Verify write succeeded - just check that something was written to the cells
        written_label = sheet.cell(next_row, 13).value
        written_amount = sheet.cell(next_row, 14).value
        
        return (written_label is not None and str(written_label).strip() != "" and
                written_amount is not None and str(written_amount).strip() != "")
    except Exception:
        return False


def parse_expense(text: str) -> tuple[float, str] | None:
    """Parse expense from text like '15 alepa' or '15.50 grocery store'."""
    match = re.match(r'^(\d+(?:[.,]\d+)?)\s+(.+)$', text.strip())
    if match:
        amount = float(match.group(1).replace(',', '.'))
        description = match.group(2).strip()
        return (amount, description)
    return None


def parse_amount(amount_str: str) -> float:
    """Convert strings like '€3.00' or '3,50' to float."""
    if amount_str is None:
        return 0.0
    cleaned = str(amount_str).strip()
    cleaned = cleaned.replace('€', '').replace(',', '.').strip()
    if not cleaned:
        return 0.0
    return float(cleaned)


# -------------------


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    await update.message.reply_text(
        '👋 Hello! I\'m your spending tracker bot.\n\n'
        '💰 To log an expense, send: <amount> <description>\n'
        'Example: 15 alepa\n\n'
        '📊 Commands:\n'
        '/history - View your spending history\n'
        '/month_total - See your total spending for the current month\n'
        '/edit - Edit a previous spending entry\n'
        '/help - Show this help message'
    )
    


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    await update.message.reply_text(
        '📖 How to use this bot:\n\n'
        '💰 Log expense: Send a number followed by description\n'
        'Example:\n'
        '  • 15 alepa\n'    
        '📊 Commands:\n'
        '/history - View recent expenses\n'
        '/month_total - See total spending for the current month\n'
        "/edit - Edit this month's expenses\n"
        '/help - Show this help message'
    )



async def month_total_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show total spending for the current month."""
    if not is_authorized(update.effective_user.id):
        return
    user_id = str(update.message.chat.id)
    data = load_spending_data()
    
    if len(data) == 0:
        await update.message.reply_text('📭 No spending history yet.')
        return

    message = 'Your recent expenses:\n\n'
    for item in data:
        label = item["label"]
        amount = item["amount"]
        message += f"• {amount} - {label}\n"

    total_spending = sum(parse_amount(item["amount"]) for item in data)
    message += f"\nTotal spending this month: €{total_spending:.2f}\n"

    await update.message.reply_text(message)



async def edit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Edit this month's expenses."""
    await update.message.reply_text('🔍 This feature is not available yet.')
     


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    
    text: str = update.message.text
    user_id = str(update.message.chat.id)
    
    print(f'User ({user_id}): "{text}"')
    
    # Try to parse as expense
    expense = parse_expense(text)
    if expense:
        amount, label = expense
        success = add_expense(user_id, amount, label)
        if not success:
            response = '❌ Failed to save expense. Please try again.'
        else:
            response = f'✅ Saved: €{amount:.2f} - {label}'
    else:
        response = (
            '❓ I didn\'t understand that.\n\n'
            'To log an expense, send: <amount> <description>\n'
            'Example: 15 alepa\n\n'
            'Type /help for more info.'
        )
    
    print(f'Bot: {response}')
    await update.message.reply_text(response)



async def error(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print(f'Update {update} caused error {context.error}')

def get_start_text() -> str:
    return (
        'Hello! I\'m your spending tracker bot.\n\n'
        'To log an expense, send: <amount> <description>\n'
        'Example: 15 alepa\n\n'
        'Commands:\n'
        '/history - View your spending history\n'
        '/month_total - See your total spending for the current month\n'
        '/edit - Edit a previous spending entry\n'
        '/help - Show this help message'
    )


def get_help_text() -> str:
    return (
        '📖 How to use this bot:\n\n'
        '💰 Log expense: Send a number followed by description\n'
        'Example:\n'
        '  • 15 alepa\n'
        '📊 Commands:\n'
        '/history - View recent expenses\n'
        '/month_total - See total spending for the current month\n'
        "/edit - Edit this month's expenses\n"
        '/help - Show this help message'
    )


def build_month_total_text() -> str:
    data = load_spending_data()
    if len(data) == 0:
        return '📭 No spending history yet.'

    message = 'Your recent expenses:\n\n'
    for item in data:
        message += f"• {item['amount']} - {item['label']}\n"

    total_spending = sum(parse_amount(item["amount"]) for item in data)
    message += f"\nTotal spending this month: €{total_spending:.2f}\n"
    return message


def load_last_update_id() -> int:
    try:
        if not os.path.exists(TELEGRAM_CURSOR_FILE):
            return 0
        with open(TELEGRAM_CURSOR_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        last_update_id = int(data.get("last_update_id", 0))
        return max(0, last_update_id)
    except Exception:
        return 0


def save_last_update_id(last_update_id: int) -> None:
    tmp_path = f"{TELEGRAM_CURSOR_FILE}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump({"last_update_id": int(last_update_id)}, f)
    os.replace(tmp_path, TELEGRAM_CURSOR_FILE)


async def process_update(bot: Bot, update: Update) -> None:
    if not update.message or not update.message.text:
        return

    if update.effective_user and not is_authorized(update.effective_user.id):
        return

    text = update.message.text
    chat_id = update.message.chat_id

    print(f'User ({chat_id}): "{text}"')

    command = text.strip().split()[0] if text.strip().startswith("/") else ""
    if command in {"/start", "/help", "/month_total", "/edit"}:
        if command == "/start":
            response = get_start_text()
        elif command == "/help":
            response = get_help_text()
        elif command == "/month_total":
            response = build_month_total_text()
        else:
            response = "🔍 This feature is not available yet."

        print(f"Bot: {response}")
        await bot.send_message(chat_id=chat_id, text=response)
        return

    expense = parse_expense(text)
    if expense:
        amount, label = expense
        success = add_expense(str(chat_id), amount, label)
        if not success:
            print("Failed to save expense.")
    else:
        print("Unrecognized message format.")


async def run_cron_drain() -> None:
    if not TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN is not set")

    bot = Bot(token=TOKEN)
    last_update_id = load_last_update_id()
    last_chat_id: int | None = None
    processed_any = False

    while True:
        updates = await bot.get_updates(offset=last_update_id + 1, timeout=0)
        if not updates:
            break

        for upd in updates:
            last_update_id = max(last_update_id, int(upd.update_id))
            if upd.message:
                last_chat_id = upd.message.chat_id
            try:
                await process_update(bot, upd)
                processed_any = True
            finally:
                save_last_update_id(last_update_id)

    if processed_any and last_chat_id is not None:
        await bot.send_message(chat_id=last_chat_id, text="All spendings are saved!")


if __name__ == '__main__':
    print("Running cron drain...")
    asyncio.run(run_cron_drain())
