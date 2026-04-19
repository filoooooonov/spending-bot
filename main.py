from typing import Final
import asyncio
import csv
import io
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

        # Color the written range (M:O) light green.
        sheet_id = sheet.id
        start_row_index = next_row - 1  # 0-based, inclusive
        end_row_index = next_row  # 0-based, exclusive
        start_col_index = 12  # M
        end_col_index = 15  # O (exclusive)
        sheet.spreadsheet.batch_update(
            {
                "requests": [
                    {
                        "repeatCell": {
                            "range": {
                                "sheetId": sheet_id,
                                "startRowIndex": start_row_index,
                                "endRowIndex": end_row_index,
                                "startColumnIndex": start_col_index,
                                "endColumnIndex": end_col_index,
                            },
                            "cell": {
                                "userEnteredFormat": {
                                    "backgroundColor": {"red": 0.85, "green": 0.95, "blue": 0.85}
                                }
                            },
                            "fields": "userEnteredFormat.backgroundColor",
                        }
                    }
                ]
            }
        )
        
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


def decode_csv_bytes(csv_bytes: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return csv_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    return csv_bytes.decode("utf-8", errors="replace")


def list_spendings_from_csv(csv_text: str) -> list[str]:
    reader = csv.DictReader(io.StringIO(csv_text), delimiter=";")
    spendings: list[str] = []

    for row in reader:
        amount_raw = (row.get("Summa") or "").strip()
        if not amount_raw.startswith("-"):
            continue

        booking_date = (row.get("Kirjauspäivä") or "").strip()
        recipient = (row.get("Saajan nimi") or "").strip()
        message = (row.get("Viesti") or "").strip()

        details = recipient or message or "Unknown"
        spendings.append(f"{booking_date} {amount_raw} {details}".strip())

    return spendings


def parse_csv_spendings(csv_text: str) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(csv_text), delimiter=";")
    spendings: list[dict[str, str]] = []

    for row in reader:
        amount_raw = (row.get("Summa") or "").strip()
        if not amount_raw:
            continue

        is_income = amount_raw.startswith("+")
        amount_value = abs(parse_amount(amount_raw))
        amount_formatted = f"+{amount_value:.2f}" if is_income else f"{amount_value:.2f}"

        spendings.append(
            {
                "date": (row.get("Kirjauspäivä") or "").strip(),
                "amount": amount_formatted,
                "type": (row.get("Tapahtumalaji") or "").strip(),
                "receiver": (row.get("Saajan nimi") or "").strip(),
            }
        )

    return spendings


def parse_sheet_date(date_str: str) -> datetime:
    cleaned = (date_str or "").strip()
    if not cleaned:
        return datetime.min
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    return datetime.min


def load_existing_csv_rows(sheet: gspread.Worksheet) -> list[dict[str, str]]:
    rows = sheet.get(range_name="R5:V")
    existing: list[dict[str, str]] = []

    for row in rows:
        padded = (row + ["", "", "", "", ""])[:5]
        item, receiver, amount, date, type_ = (cell.strip() for cell in padded)

        if not any([item, receiver, amount, date, type_]):
            continue

        existing.append(
            {"item": item, "receiver": receiver, "amount": amount, "date": date, "type": type_}
        )

    return existing


def write_csv_rows_sorted(sheet: gspread.Worksheet, rows: list[dict[str, str]]) -> None:
    values: list[list[object]] = []
    for r in rows:
        values.append([r.get("item", ""), r.get("receiver", ""), r.get("amount", ""), r.get("date", ""), r.get("type", "")])

    if len(values) == 0:
        return

    start_row = 5
    end_row = start_row + len(values) - 1
    sheet.update(range_name=f"R{start_row}:V{end_row}", values=values)

    # Clear any leftover old rows below, so deleted rows don't linger.
    col_s = sheet.col_values(19)  # S (Receiver)
    previous_last_row = max(4, len(col_s))
    if previous_last_row > end_row:
        sheet.update(
            range_name=f"R{end_row + 1}:V{previous_last_row}",
            values=[["", "", "", "", ""] for _ in range(previous_last_row - end_row)],
        )

    # Color the written range (R:V) light blue.
    light_blue = {"red": 0.8, "green": 0.9, "blue": 1.0}
    sheet_id = sheet.id
    sheet.spreadsheet.batch_update(
        {
            "requests": [
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": start_row - 1,  # 0-based
                            "endRowIndex": end_row,
                            "startColumnIndex": 17,  # R
                            "endColumnIndex": 22,  # V (exclusive)
                        },
                        "cell": {"userEnteredFormat": {"backgroundColor": light_blue}},
                        "fields": "userEnteredFormat.backgroundColor",
                    }
                }
            ]
        }
    )


def add_and_sort_csv_spendings_to_sheet(new_spendings: list[dict[str, str]]) -> int:
    sheet = get_current_sheet()
    existing = load_existing_csv_rows(sheet)

    incoming_rows: list[dict[str, str]] = []
    for item in new_spendings:
        incoming_rows.append(
            {
                "item": "",  # keep empty for manual input
                "receiver": item["receiver"],
                "amount": item["amount"],
                "date": item["date"],
                "type": item["type"],
            }
        )

    merged = existing + incoming_rows
    merged.sort(key=lambda r: parse_sheet_date(r.get("date", "")))

    write_csv_rows_sorted(sheet, merged)
    return len(incoming_rows)


def ensure_sheet_headers() -> None:
    sheet = get_current_sheet()
    sheet_id = sheet.id

    light_green = {"red": 0.85, "green": 0.95, "blue": 0.85}
    light_blue = {"red": 0.8, "green": 0.9, "blue": 1.0}

    sheet.spreadsheet.batch_update(
        {
            "requests": [
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": 3,  # row 4
                            "endRowIndex": 4,
                            "startColumnIndex": 12,  # M
                            "endColumnIndex": 15,  # O (exclusive)
                        },
                        "cell": {"userEnteredFormat": {"backgroundColor": light_green}},
                        "fields": "userEnteredFormat.backgroundColor",
                    }
                },
                {
                    "updateCells": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": 3,  # row 4
                            "endRowIndex": 4,
                            "startColumnIndex": 12,  # M
                            "endColumnIndex": 13,
                        },
                        "rows": [
                            {
                                "values": [
                                    {
                                        "userEnteredValue": {"stringValue": "Expenses in cash"},
                                        "userEnteredFormat": {
                                            "textFormat": {"bold": True},
                                        },
                                    }
                                ]
                            }
                        ],
                        "fields": "userEnteredValue,userEnteredFormat.textFormat.bold",
                    }
                },
                {
                    "updateCells": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": 3,  # row 4
                            "endRowIndex": 4,
                            "startColumnIndex": 17,  # R
                            "endColumnIndex": 22,  # V (exclusive)
                        },
                        "rows": [
                            {
                                "values": [
                                    {
                                        "userEnteredValue": {"stringValue": "Item"},
                                        "userEnteredFormat": {
                                            "backgroundColor": light_blue,
                                            "textFormat": {"bold": True},
                                        },
                                    },
                                    {
                                        "userEnteredValue": {"stringValue": "Receiver"},
                                        "userEnteredFormat": {
                                            "backgroundColor": light_blue,
                                            "textFormat": {"bold": True},
                                        },
                                    },
                                    {
                                        "userEnteredValue": {"stringValue": "Amount"},
                                        "userEnteredFormat": {
                                            "backgroundColor": light_blue,
                                            "textFormat": {"bold": True},
                                        },
                                    },
                                    {
                                        "userEnteredValue": {"stringValue": "Date"},
                                        "userEnteredFormat": {
                                            "backgroundColor": light_blue,
                                            "textFormat": {"bold": True},
                                        },
                                    },
                                    {
                                        "userEnteredValue": {"stringValue": "Type"},
                                        "userEnteredFormat": {
                                            "backgroundColor": light_blue,
                                            "textFormat": {"bold": True},
                                        },
                                    },
                                ]
                            }
                        ],
                        "fields": "userEnteredValue,userEnteredFormat(backgroundColor,textFormat.bold)",
                    }
                },
            ]
        }
    )


def chunk_lines(lines: list[str], header: str, max_chars: int = 3500) -> list[str]:
    # Telegram max is 4096; keep buffer for safety.
    chunks: list[str] = []
    current = header.strip() + "\n"

    for line in lines:
        next_piece = f"- {line}\n"
        if len(current) + len(next_piece) > max_chars and current.strip():
            chunks.append(current.strip())
            current = header.strip() + "\n" + next_piece
        else:
            current += next_piece

    if current.strip():
        chunks.append(current.strip())
    return chunks


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


async def process_update(bot: Bot, update: Update) -> bool:
    if not update.message:
        return False

    if update.effective_user and not is_authorized(update.effective_user.id):
        return False

    chat_id = update.message.chat_id

    if update.message.document and update.message.document.file_name:
        file_name = update.message.document.file_name.strip()
        if file_name.lower().endswith(".csv"):
            tg_file = await bot.get_file(update.message.document.file_id)
            csv_bytes = await tg_file.download_as_bytearray()
            csv_text = decode_csv_bytes(bytes(csv_bytes))
            spendings = parse_csv_spendings(csv_text)
            uploaded_count = add_and_sort_csv_spendings_to_sheet(spendings)
            if uploaded_count == 0:
                await bot.send_message(chat_id=chat_id, text="CSV received, but no spendings found.")
            else:
                await bot.send_message(
                    chat_id=chat_id,
                    text=f"Successfully uploaded the csv to Google Sheets. ({uploaded_count} rows)",
                )
            return False

    if not update.message.text:
        return False

    text = update.message.text

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
        return False

    expense = parse_expense(text)
    if expense:
        amount, label = expense
        success = add_expense(str(chat_id), amount, label)
        if not success:
            print("Failed to save expense.")
            return False
        return True
    else:
        print("Unrecognized message format.")
        return False


async def run_cron_drain() -> None:
    if not TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN is not set")

    bot = Bot(token=TOKEN)
    ensure_sheet_headers()
    last_update_id = load_last_update_id()
    last_spending_chat_id: int | None = None
    saved_any_spending = False

    while True:
        updates = await bot.get_updates(offset=last_update_id + 1, timeout=0)
        if not updates:
            break

        for upd in updates:
            last_update_id = max(last_update_id, int(upd.update_id))
            try:
                saved_spending = await process_update(bot, upd)
                if saved_spending and upd.message:
                    last_spending_chat_id = upd.message.chat_id
                    saved_any_spending = True
            finally:
                save_last_update_id(last_update_id)

    if saved_any_spending and last_spending_chat_id is not None:
        await bot.send_message(chat_id=last_spending_chat_id, text="All spendings are saved!")


if __name__ == '__main__':
    print("Running cron drain...")
    asyncio.run(run_cron_drain())
