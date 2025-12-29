from typing import Final
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv
import os
import json
import re
import base64
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime


load_dotenv()

scopes = [
    "https://www.googleapis.com/auth/spreadsheets"
]

sheet_id = "14LYEWi4vJi261oTxE1HH4TxluTYcVg4zWDok8IwbJc4"

# Lazy initialization to avoid Railway accessing env vars during build
_client = None
_workbook = None

def get_client():
    """Get or create the gspread client (lazy initialization)."""
    global _client
    if _client is None:
        # Load credentials from environment variable (Railway) or file (local)
        google_credentials_json = os.environ.get('GOOGLE_CREDENTIALS_JSON')
        if google_credentials_json:
            try:
                # Try base64 decode first (for Railway to avoid build-time parsing issues)
                try:
                    decoded = base64.b64decode(google_credentials_json).decode('utf-8')
                    creds_info = json.loads(decoded)
                except Exception:
                    # If base64 decode fails, treat as plain JSON
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
                "Set GOOGLE_CREDENTIALS_JSON environment variable (base64 encoded or JSON) or provide credentials.json file."
            )
        _client = gspread.authorize(creds)
    return _client

def get_workbook():
    """Get or create the workbook (lazy initialization)."""
    global _workbook
    if _workbook is None:
        _workbook = get_client().open_by_key(sheet_id)
    return _workbook


TOKEN: Final = os.environ.get('TELEGRAM_BOT_TOKEN')
BOT_USERNAME: Final = os.environ.get('TELEGRAM_BOT_USERNAME', '@AlekseiFilonovSpendingBot')
ALLOWED_USER_ID: Final = os.environ.get('ALLOWED_USER_ID')
SPENDING_DATA_FILE: Final = 'spending_data.json'
INCOME_DATA_FILE: Final = 'income_data.json'


def get_current_sheet() -> gspread.Worksheet:
    """Get the current sheet for the current month."""
    # TODO: uncomment this for deployment
    # current_month = datetime.now().strftime("%B")
    current_month = "January"
    return get_workbook().worksheet(current_month)


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
    spending_values = {}
    for i in range(max(len(spending_labels), len(spending_amounts))):
        label = spending_labels[i] if i < len(spending_labels) else ""
        amount = spending_amounts[i] if i < len(spending_amounts) else ""
        
        # Only add to dict if label is not empty
        if label.strip():
            spending_values[label] = amount
    
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
        
        # Write to both columns
        sheet.update(range_name=f"M{next_row}:N{next_row}", values=[[label, amount]])
        
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
    for label, amount_str in data.items():
        message += f"• {amount_str} - {label}\n"
        
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

if __name__ == '__main__':
    print('Starting bot...')
    app = Application.builder().token(TOKEN).build()

    # Commands
    app.add_handler(CommandHandler('start', start_command))
    app.add_handler(CommandHandler('help', help_command))
    app.add_handler(CommandHandler('month_total', month_total_command))
    app.add_handler(CommandHandler('edit', edit_command))

    # Messages
    app.add_handler(MessageHandler(filters.TEXT, handle_message))

    # Errors
    app.add_error_handler(error)

    # Polls the bot
    print('Polling...')
    app.run_polling(poll_interval=3)    
