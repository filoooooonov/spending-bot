import json
import os
import time
import urllib.error
import urllib.request

from category_cache import normalize_receiver


CATEGORIES: list[str] = [
    "Food",
    "Transport",
    "Stud. Restaurants",
    "Coffee & Snacks",
    "Savings",
    "Clothes",
    "Family",
    "Parties/hangouts",
    "Taxi",
    "Eating out",
    "Gifts",
    "Activities",
    "Other",
    "Subscription",
    "Travel",
    "Electronics",
    "Internet",
    "Rent",
    "Needs",
]

NEEDS_WANTS_MAP: dict[str, str] = {
    "Food": "Needs",
    "Rent": "Needs",
    "Transport": "Needs",
    "Internet": "Needs",
    "Stud. Restaurants": "Wants",
    "Coffee & Snacks": "Wants",
    "Eating out": "Wants",
    "Clothes": "Wants",
    "Parties/hangouts": "Wants",
    "Taxi": "Wants",
    "Gifts": "Wants",
    "Activities": "Wants",
    "Electronics": "Wants",
    "Travel": "Wants",
    "Subscription": "Wants",
    "Savings": "Savings",
    "Family": "Needs",
    "Other": "Other",
    "Needs": "Needs",
}


def require_anthropic_api_key() -> str:
    api_key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY is not set")
    return api_key


def build_categorizer_system_prompt() -> str:
    return (
        "You are a transaction categorizer. Given merchant names/amounts from a Finnish person's bank statement, "
        "assign each one a category.\n"
        f"Categories: {', '.join(CATEGORIES)}.\n"
        'Respond ONLY with valid JSON array of objects with keys "id" and "category". No markdown, no explanation.\n'
        'Example: [{"id":0,"category":"Food"},{"id":1,"category":"Transport"}]\n'
        "Rules:\n"
        "- K-market, Alepa, S-market, Lidl, Prisma = Food\n"
        "- HSL, bus, metro, train = Transport\n"
        "- Compass Group, Menssa = Stud. Restaurants\n"
        "- NYX*JOBmeal, coffee shops = Coffee & Snacks\n"
        "- Restaurants not student = Eating out\n"
        "- Clothing stores = Clothes\n"
        "- Power, Stockma, electronics shops = Electronics\n"
        "- Travel abroad merchants (Target, Whole Foods, Chipotle, SFO, etc.) = Travel\n"
        "- Unknown = Other"
    )


def _anthropic_messages(api_key: str, payload: dict) -> dict:
    req = urllib.request.Request(
        url="https://api.anthropic.com/v1/messages",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body)


def extract_text_from_anthropic_response(data: dict) -> str:
    content = data.get("content") or []
    chunks: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            chunks.append(str(block.get("text") or ""))
    return "".join(chunks).strip()


def parse_categories_json(text: str) -> list[dict]:
    cleaned = text.replace("```json", "").replace("```", "").strip()
    parsed = json.loads(cleaned)
    if not isinstance(parsed, list):
        raise ValueError("Claude response JSON is not an array")
    return parsed


def enrich_spendings_from_category_map(
    spendings: list[dict[str, str]], receiver_to_category: dict[str, str]
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    enriched: list[dict[str, str]] = []
    unknown: list[dict[str, str]] = []

    for s in spendings:
        receiver_key = normalize_receiver((s.get("receiver") or "").strip())
        category = (receiver_to_category.get(receiver_key) or "").strip()
        if not category:
            unknown.append(s)
            continue

        needs_wants = NEEDS_WANTS_MAP.get(category) or "Other"
        enriched.append({**s, "category": category, "needsWants": needs_wants})

    return enriched, unknown


def categorize_spendings_with_claude(spendings: list[dict[str, str]]) -> list[dict[str, str]]:
    api_key = require_anthropic_api_key()

    merchant_list = "\n".join(
        f"{i}: {(s.get('receiver') or '').strip()} {(s.get('amount') or '').strip()}".strip()
        for i, s in enumerate(spendings)
    )

    payload = {
        "model": os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
        "max_tokens": 1500,
        "system": build_categorizer_system_prompt(),
        "messages": [{"role": "user", "content": f"Categorize these transactions:\n{merchant_list}"}],
    }

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            data = _anthropic_messages(api_key, payload)
            text = extract_text_from_anthropic_response(data)
            cats = parse_categories_json(text)

            cat_map: dict[int, str] = {}
            for c in cats:
                if not isinstance(c, dict):
                    continue
                id_raw = c.get("id")
                category = str(c.get("category") or "").strip()
                if isinstance(id_raw, int) and category:
                    cat_map[id_raw] = category

            enriched: list[dict[str, str]] = []
            for i, s in enumerate(spendings):
                category = cat_map.get(i) or "Other"
                needs_wants = NEEDS_WANTS_MAP.get(category) or "Other"
                enriched.append({**s, "category": category, "needsWants": needs_wants})
            return enriched
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as e:
            last_error = e
            time.sleep(1.0 + attempt * 1.5)

    raise RuntimeError(f"Failed to categorize via Claude: {last_error}")

