from typing import Final
import asyncio
import csv
import io
import sys
import httpx

# Render captures stdout through a pipe, which block-buffers by default — flush
# each line so debug prints show up immediately.
sys.stdout.reconfigure(line_buffering=True)
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
from datetime import timezone

from category_cache import load_category_cache, normalize_receiver, save_category_cache


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
# Finance tracker (Convex) — mirror each cash expense into its ledger.
TRACKER_CASH_URL: Final = os.environ.get('TRACKER_CASH_URL')
TRACKER_CASH_SECRET: Final = os.environ.get('TRACKER_CASH_SECRET')
SPENDING_DATA_FILE: Final = 'spending_data.json'
TELEGRAM_CURSOR_FILE: Final = os.environ.get("TELEGRAM_CURSOR_FILE", "telegram_cursor.json")


def normalize_month_name(month: str) -> str:
    """Normalize user input to an English month name (e.g. '3' -> 'March', 'sep' -> 'September')."""
    cleaned = (month or "").strip()
    if not cleaned:
        raise ValueError("Month is required")

    lower = cleaned.lower()

    month_by_number: dict[str, str] = {
        "1": "January",
        "01": "January",
        "2": "February",
        "02": "February",
        "3": "March",
        "03": "March",
        "4": "April",
        "04": "April",
        "5": "May",
        "05": "May",
        "6": "June",
        "06": "June",
        "7": "July",
        "07": "July",
        "8": "August",
        "08": "August",
        "9": "September",
        "09": "September",
        "10": "October",
        "11": "November",
        "12": "December",
    }
    if lower in month_by_number:
        return month_by_number[lower]

    month_by_name: dict[str, str] = {
        "jan": "January",
        "january": "January",
        "feb": "February",
        "february": "February",
        "mar": "March",
        "march": "March",
        "apr": "April",
        "april": "April",
        "may": "May",
        "jun": "June",
        "june": "June",
        "jul": "July",
        "july": "July",
        "aug": "August",
        "august": "August",
        "sep": "September",
        "sept": "September",
        "september": "September",
        "oct": "October",
        "october": "October",
        "nov": "November",
        "november": "November",
        "dec": "December",
        "december": "December",
    }
    if lower in month_by_name:
        return month_by_name[lower]

    # Last resort: Title-case input (useful if user already typed full month).
    candidate = cleaned[:1].upper() + cleaned[1:].lower()
    return candidate


def get_sheet_for_month(month: str) -> gspread.Worksheet:
    """Get the worksheet for a specified month name."""
    month_name = normalize_month_name(month)
    return workbook.worksheet(month_name)


def get_current_sheet() -> gspread.Worksheet:
    """Get the current sheet for the current month."""
    return get_sheet_for_month(datetime.now().strftime("%B"))


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


async def forward_to_tracker(amount: float, label: str, message_id: int | None) -> str:
    """Mirror a logged expense into the finance tracker (best-effort — a tracker
    outage must never break the Sheets logging, which is the source of truth).
    Returns a short status string that gets appended to the Telegram reply, so
    the outcome is visible without digging through host logs."""
    if not TRACKER_CASH_URL or not TRACKER_CASH_SECRET:
        missing = []
        if not TRACKER_CASH_URL:
            missing.append("TRACKER_CASH_URL")
        if not TRACKER_CASH_SECRET:
            missing.append("TRACKER_CASH_SECRET")
        return f"⚠️ tracker: env not set ({', '.join(missing)})"
    payload: dict = {
        "amount": amount,
        "description": label,
        "date": datetime.now().strftime("%Y-%m-%d"),
    }
    if message_id is not None:
        payload["externalId"] = f"telegram:{message_id}"
    try:
        async with httpx.AsyncClient(timeout=10) as http_client:
            resp = await http_client.post(
                TRACKER_CASH_URL,
                json=payload,
                headers={"X-Tracker-Secret": TRACKER_CASH_SECRET},
            )
            if resp.status_code >= 300:
                return f"⚠️ tracker: {resp.status_code} {resp.text[:100]}"
            return "→ tracker ✓"
    except Exception as e:
        return f"⚠️ tracker error: {type(e).__name__}: {e}"


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
            tracker = await forward_to_tracker(amount, label, update.message.message_id)
            response = f'✅ Saved: €{amount:.2f} - {label}\n{tracker}'
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


def load_receiver_category_map_from_sheet(sheet: gspread.Worksheet) -> dict[str, str]:
    existing = load_existing_csv_rows(sheet)
    receiver_to_category: dict[str, str] = {}

    for r in existing:
        receiver = (r.get("receiver") or "").strip()
        category = (r.get("item") or "").strip()
        if not receiver or not category:
            continue
        receiver_to_category[normalize_receiver(receiver)] = category

    return receiver_to_category


def categorize_spendings_using_sheet_cache(
    sheet: gspread.Worksheet, spendings: list[dict[str, str]]
) -> list[dict[str, str]]:
    if not spendings:
        return []

    receiver_to_category = load_category_cache()
    receiver_to_category |= load_receiver_category_map_from_sheet(sheet)

    # Import lazily so the bot can run without Anthropic configured.
    from claude_categorizer import NEEDS_WANTS_MAP, categorize_spendings_with_claude, enrich_spendings_from_category_map

    _, unknown = enrich_spendings_from_category_map(spendings, receiver_to_category)

    if unknown:
        try:
            newly = categorize_spendings_with_claude(unknown)
            for s in newly:
                receiver_key = normalize_receiver((s.get("receiver") or "").strip())
                category = (s.get("category") or "").strip()
                if receiver_key and category:
                    receiver_to_category[receiver_key] = category
            save_category_cache(receiver_to_category)
        except Exception:
            # If Claude is unavailable, we still upload what we can (and keep unknown categories empty).
            newly = []

    # Re-apply the final mapping (sheet+cache+newly), preserving original order.
    final: list[dict[str, str]] = []
    for s in spendings:
        receiver_key = normalize_receiver((s.get("receiver") or "").strip())
        category = (receiver_to_category.get(receiver_key) or "").strip()
        if not category:
            final.append(s)
            continue
        needs_wants = NEEDS_WANTS_MAP.get(category) or "Other"
        final.append({**s, "category": category, "needsWants": needs_wants})

    save_category_cache(receiver_to_category)
    return final



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


def add_and_sort_csv_spendings_to_sheet_on(sheet: gspread.Worksheet, new_spendings: list[dict[str, str]]) -> int:
    existing = load_existing_csv_rows(sheet)

    incoming_rows: list[dict[str, str]] = []
    for item in new_spendings:
        incoming_rows.append(
            {
                "item": (item.get("category") or "").strip(),  # category if provided; otherwise keep empty
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


def add_and_sort_csv_spendings_to_sheet(new_spendings: list[dict[str, str]]) -> int:
    return add_and_sort_csv_spendings_to_sheet_on(get_current_sheet(), new_spendings)


def ensure_sheet_headers_on(sheet: gspread.Worksheet) -> None:
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


def ensure_sheet_headers() -> None:
    ensure_sheet_headers_on(get_current_sheet())


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
            spendings = categorize_spendings_using_sheet_cache(get_current_sheet(), spendings)
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


def _start_of_current_month_utc() -> datetime:
    now = datetime.now(timezone.utc)
    return datetime(year=now.year, month=now.month, day=1, tzinfo=timezone.utc)


async def handle_csv_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    if update.effective_user and not is_authorized(update.effective_user.id):
        return
    if not update.message.document or not update.message.document.file_name:
        return

    file_name = update.message.document.file_name.strip()
    if not file_name.lower().endswith(".csv"):
        return

    ensure_sheet_headers()

    tg_file = await context.bot.get_file(update.message.document.file_id)
    csv_bytes = await tg_file.download_as_bytearray()
    csv_text = decode_csv_bytes(bytes(csv_bytes))

    spendings = parse_csv_spendings(csv_text)
    spendings = categorize_spendings_using_sheet_cache(get_current_sheet(), spendings)
    uploaded_count = add_and_sort_csv_spendings_to_sheet(spendings)

    if uploaded_count == 0:
        await update.message.reply_text("CSV received, but no spendings found.")
        return

    await update.message.reply_text(f"Successfully uploaded the csv to Google Sheets. ({uploaded_count} rows)")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    if update.effective_user and not is_authorized(update.effective_user.id):
        return
    if not update.message.text:
        return

    # Ignore very old messages (e.g. after webhook downtime / retries) outside current month.
    # This keeps sheet inserts stable if Telegram retries stale deliveries.
    if update.message.date and update.message.date < _start_of_current_month_utc():
        return

    text: str = update.message.text
    user_id = str(update.message.chat.id)

    print(f'User ({user_id}): "{text}"')

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
        await update.message.reply_text(response)
        return

    expense = parse_expense(text)
    if not expense:
        await update.message.reply_text(
            '❓ I didn\'t understand that.\n\n'
            'To log an expense, send: <amount> <description>\n'
            'Example: 15 alepa\n\n'
            'Type /help for more info.'
        )
        return

    amount, label = expense
    success = add_expense(user_id, amount, label)
    if not success:
        await update.message.reply_text("❌ Failed to save expense. Please try again.")
        return

    tracker = await forward_to_tracker(amount, label, update.message.message_id)
    await update.message.reply_text(f"✅ Saved: €{amount:.2f} - {label}\n{tracker}")


def run_webhook_server() -> None:
    """
    Recommended for Render: long-lived service + Telegram webhook delivery (no polling gaps).

    Required env:
      - TELEGRAM_BOT_TOKEN
      - PUBLIC_URL (e.g. https://your-service.onrender.com)
      - PORT (Render provides)
    Optional:
      - WEBHOOK_PATH (default: /telegram-webhook)
    """
    if not TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN is not set")

    public_url = (os.environ.get("PUBLIC_URL") or "").strip().rstrip("/")
    if not public_url:
        raise ValueError("PUBLIC_URL is not set (e.g. https://your-service.onrender.com)")

    port = int(os.environ.get("PORT") or "10000")
    webhook_path = (os.environ.get("WEBHOOK_PATH") or "/telegram-webhook").strip()
    if not webhook_path.startswith("/"):
        webhook_path = f"/{webhook_path}"

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("month_total", month_total_command))
    app.add_handler(CommandHandler("edit", edit_command))

    app.add_handler(MessageHandler(filters.Document.ALL, handle_csv_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.add_error_handler(error)

    # NOTE: python-telegram-bot's run_webhook manages the asyncio loop internally.
    # Don't wrap this in asyncio.run() / don't await it.
    app.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=webhook_path.lstrip("/"),
        webhook_url=f"{public_url}{webhook_path}",
        drop_pending_updates=False,
    )


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
    run_mode = (os.environ.get("RUN_MODE") or "cron").strip().lower()
    if run_mode == "webhook":
        print("Running webhook server...")
        run_webhook_server()
    else:
        print("Running cron drain...")
        asyncio.run(run_cron_drain())
