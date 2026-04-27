import os
import sys

from main import (
    add_and_sort_csv_spendings_to_sheet_on,
    categorize_spendings_using_sheet_cache,
    decode_csv_bytes,
    ensure_sheet_headers_on,
    get_sheet_for_month,
    parse_csv_spendings,
)


def parse_args(argv: list[str]) -> dict[str, str | None]:
    month: str | None = None
    csv_path: str | None = None

    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in {"--month", "-m"}:
            i += 1
            month = argv[i] if i < len(argv) else None
        elif arg in {"--csv", "--file", "-f"}:
            i += 1
            csv_path = argv[i] if i < len(argv) else None
        elif arg in {"--help", "-h"}:
            print(
                "Usage:\n"
                "  python upload_csv_month.py\n"
                "  python upload_csv_month.py --month March --csv path/to/file.csv\n\n"
                "Month accepts: 1-12, Jan/January, Sep/September, etc."
            )
            raise SystemExit(0)
        i += 1

    return {"month": month, "csv_path": csv_path}


def prompt_non_empty(prompt: str) -> str:
    while True:
        value = input(prompt).strip()
        if value:
            return value


def read_csv_bytes(csv_path: str) -> bytes:
    with open(csv_path, "rb") as f:
        return f.read()


def main() -> None:
    args = parse_args(sys.argv[1:])

    month = args["month"] or prompt_non_empty("Which month do you want to add data to? (e.g. 3, March, Sep): ")
    csv_path = args["csv_path"] or prompt_non_empty("Path to CSV file: ")

    csv_path = os.path.expandvars(os.path.expanduser(csv_path))
    if not os.path.exists(csv_path):
        raise SystemExit(f"CSV file not found: {csv_path}")

    sheet = get_sheet_for_month(month)
    ensure_sheet_headers_on(sheet)

    csv_text = decode_csv_bytes(read_csv_bytes(csv_path))
    spendings = parse_csv_spendings(csv_text)
    spendings = categorize_spendings_using_sheet_cache(sheet, spendings)
    uploaded_count = add_and_sort_csv_spendings_to_sheet_on(sheet, spendings)

    if uploaded_count == 0:
        print("CSV loaded, but no spendings found to upload.")
        return

    print(f"Uploaded {uploaded_count} rows to sheet tab '{sheet.title}'.")


if __name__ == "__main__":
    main()

