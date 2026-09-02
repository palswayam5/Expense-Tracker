"""
Gmail-based transaction importer for PayTM, GPay, and CRED.

Setup (one-time):
  1. Go to https://console.cloud.google.com
  2. Create a project → Enable "Gmail API"
  3. Create OAuth 2.0 credentials (Desktop app) → download as credentials.json
  4. Place credentials.json in this directory
  5. pip install google-auth-httplib2 google-auth-oauthlib google-api-python-client

First run opens a browser for Google sign-in; token is saved to token.json after that.
"""

import re
import base64
import json
from datetime import datetime, date
from pathlib import Path
from typing import Optional

from main import ExpenseTracker, Expense

# Gmail API imports — install via pip if missing
try:
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
except ImportError:
    raise SystemExit(
        "Missing dependencies. Run:\n"
        "  pip install google-auth-httplib2 google-auth-oauthlib google-api-python-client"
    )

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CREDENTIALS_FILE = Path("credentials.json")
TOKEN_FILE = Path("token.json")


# ── Gmail auth ────────────────────────────────────────────────────────────────

def get_gmail_service():
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDENTIALS_FILE.exists():
                raise FileNotFoundError(
                    "credentials.json not found. Download it from Google Cloud Console."
                )
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json())

    return build("gmail", "v1", credentials=creds)


# ── Email fetching ────────────────────────────────────────────────────────────

def _decode_body(payload: dict, mime: str = "text/plain") -> str:
    """Recursively extract a specific MIME part from a Gmail message payload."""
    if payload.get("mimeType") == mime:
        data = payload.get("body", {}).get("data", "")
        return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")
    for part in payload.get("parts", []):
        result = _decode_body(part, mime)
        if result:
            return result
    return ""


def _html_to_text(html: str) -> str:
    """Strip HTML tags and decode entities to get plain readable text."""
    import html as html_mod
    # Drop script / style blocks entirely
    text = re.sub(r"<(script|style)[^>]*>.*?</(script|style)>", "", html,
                  flags=re.DOTALL | re.IGNORECASE)
    # Replace common block tags with newlines so amounts stay on separate lines
    text = re.sub(r"<(?:br|p|div|tr|td|th|li|h\d)[^>]*>", "\n", text, flags=re.IGNORECASE)
    # Strip remaining tags
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_mod.unescape(text)
    # Collapse whitespace
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _get_body(payload: dict) -> str:
    """Return plain text body; fall back to HTML-stripped if no plain text part."""
    plain = _decode_body(payload, "text/plain")
    if plain.strip():
        return plain
    html = _decode_body(payload, "text/html")
    return _html_to_text(html) if html else ""


def fetch_emails(service, query: str, max_results: int = 50) -> list[dict]:
    """Return list of {id, subject, from, date, body} dicts matching a Gmail query."""
    result = service.users().messages().list(
        userId="me", q=query, maxResults=max_results
    ).execute()

    messages = result.get("messages", [])
    emails = []
    for msg in messages:
        full = service.users().messages().get(
            userId="me", id=msg["id"], format="full"
        ).execute()
        headers = {h["name"]: h["value"] for h in full["payload"]["headers"]}
        emails.append({
            "id":      msg["id"],
            "subject": headers.get("Subject", ""),
            "from":    headers.get("From", ""),
            "date":    headers.get("Date", ""),
            "body":    _get_body(full["payload"]),
        })
    return emails


# ── Amount / date helpers ─────────────────────────────────────────────────────

_AMOUNT_RE = re.compile(r"(?:Rs\.?|INR|₹)\s*([\d,]+(?:\.\d{1,2})?)", re.IGNORECASE)
_DATE_FORMATS = [
    "%d %b %Y", "%d %B %Y",
    "%b %d, %Y", "%B %d, %Y",
    "%b %d %Y", "%B %d %Y",
    "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d",
]


def _parse_amount(text: str) -> Optional[float]:
    match = _AMOUNT_RE.search(text)
    if match:
        return float(match.group(1).replace(",", ""))
    return None


def _parse_date(text: str) -> str:
    # Strip ordinal suffixes: "1st" → "1", "2nd" → "2", etc.
    text = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", text).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return date.today().isoformat()


# ── Per-app parsers ───────────────────────────────────────────────────────────

class ParsedTransaction:
    def __init__(self, amount: float, merchant: str, txn_date: str,
                 source: str, raw_subject: str):
        self.amount = amount
        self.merchant = merchant
        self.date = txn_date
        self.source = source        # "paytm" | "gpay" | "cred"
        self.raw_subject = raw_subject


def parse_paytm(email: dict) -> Optional[ParsedTransaction]:
    """
    PayTM sends emails like:
      Subject: "Paytm Transaction Alert"
      Body:    "You have successfully paid Rs. 250.00 to Zomato"
               "Transaction Date: 30 Aug 2026"
    """
    body = email["body"]
    subject = email["subject"]

    amount = _parse_amount(body) or _parse_amount(subject)
    if not amount:
        return None

    merchant = "Unknown"
    m = re.search(r"paid\s+(?:Rs\.?|INR|₹)\s*[\d,.]+\s+to\s+(.+?)(?:\n|\.|\bon\b)", body, re.IGNORECASE)
    if m:
        merchant = m.group(1).strip()

    txn_date = date.today().isoformat()
    m = re.search(r"(?:Transaction\s+Date|Date)\s*[:\-]\s*(.+?)(?:\n|$)", body, re.IGNORECASE)
    if m:
        txn_date = _parse_date(m.group(1))

    return ParsedTransaction(amount, merchant, txn_date, "paytm", subject)


def parse_gpay(email: dict) -> Optional[ParsedTransaction]:
    """
    GPay sends emails like:
      Subject: "You paid ₹500 to Amazon"
      Body:    confirmation details
    """
    subject = email["subject"]
    body = email["body"]

    # Try subject first ("You paid ₹500 to Amazon")
    m = re.search(
        r"(?:You paid|Payment of)\s+(?:Rs\.?|INR|₹)\s*([\d,]+(?:\.\d{1,2})?)\s+to\s+(.+?)(?:\s+on\b|$)",
        subject, re.IGNORECASE,
    )
    if m:
        amount = float(m.group(1).replace(",", ""))
        merchant = m.group(2).strip()
    else:
        amount = _parse_amount(body) or _parse_amount(subject)
        if not amount:
            return None
        merchant_m = re.search(r"to\s+([A-Za-z0-9 &'.\-]+?)(?:\n|\.|\bon\b|$)", body)
        merchant = merchant_m.group(1).strip() if merchant_m else "Unknown"

    txn_date = date.today().isoformat()
    date_m = re.search(r"(\d{1,2}\s+\w+\s+\d{4}|\d{4}-\d{2}-\d{2})", body)
    if date_m:
        txn_date = _parse_date(date_m.group(1))

    return ParsedTransaction(amount, merchant, txn_date, "gpay", subject)


def parse_cred(email: dict) -> Optional[ParsedTransaction]:
    """
    CRED sends emails for:
      - Credit card bill payments: "Payment of ₹1,200 for HDFC Credit Card"
      - Cashback (skip these — not an expense)
    """
    body = email["body"]
    subject = email["subject"]

    # Skip cashback / rewards emails
    if re.search(r"cashback|reward|coins|earned", subject + body, re.IGNORECASE):
        return None

    amount = _parse_amount(body) or _parse_amount(subject)
    if not amount:
        return None

    merchant = "Credit Card Payment"
    m = re.search(r"for\s+([A-Za-z0-9 &'.\-]+ Card)", body, re.IGNORECASE)
    if m:
        merchant = m.group(1).strip()

    txn_date = date.today().isoformat()
    date_m = re.search(r"(\d{1,2}\s+\w+\s+\d{4}|\d{4}-\d{2}-\d{2})", body)
    if date_m:
        txn_date = _parse_date(date_m.group(1))

    return ParsedTransaction(amount, merchant, txn_date, "cred", subject)


def parse_uber(email: dict) -> Optional[ParsedTransaction]:
    """
    Uber sends HTML receipt emails.
      From:    uber@uber.com  (or noreply@uber.com)
      Subject: "Your Tuesday trip with Uber"
      Body:    contains "Total  ₹XXX" and trip date
    """
    body    = email["body"]
    subject = email["subject"]

    # Skip Uber Eats — handled separately as Food
    if re.search(r"eats|food|order", subject, re.IGNORECASE):
        return None

    amount = _parse_amount(body) or _parse_amount(subject)
    if not amount:
        return None

    # Route description: "Sector 62 → Sector 55"
    merchant = "Uber Ride"
    route = re.search(r"([A-Za-z0-9 ,]+)\s*(?:→|->|to)\s*([A-Za-z0-9 ,]+)", body)
    if route:
        merchant = f"Uber: {route.group(1).strip()} → {route.group(2).strip()}"

    txn_date = date.today().isoformat()
    date_m = re.search(r"(\w+ \d{1,2},?\s*\d{4}|\d{1,2} \w+ \d{4}|\d{4}-\d{2}-\d{2})", body)
    if date_m:
        txn_date = _parse_date(date_m.group(1))

    return ParsedTransaction(amount, merchant, txn_date, "uber", subject)


def parse_rapido(email: dict) -> Optional[ParsedTransaction]:
    """
    Rapido invoice emails from partner@rapido.bike.
      Subject: "Rapido Invoice"
      Body:    Total Amount ₹ 75.00, date "Sep 1st 2026", two address lines for route
    """
    body    = email["body"]
    subject = email["subject"]

    # Prefer "Total Amount ₹ X" line to avoid picking up sub-charges
    total_m = re.search(
        r"Total\s+Amount\s*[₹Rs.]*\s*([\d,]+(?:\.\d{1,2})?)", body, re.IGNORECASE
    )
    amount = float(total_m.group(1).replace(",", "")) if total_m else _parse_amount(body)
    if not amount:
        return None

    # Route: extract Sector numbers from the two address lines
    ride_desc = "Rapido Ride"
    sectors = re.findall(r"Sector\s+\d+", body, re.IGNORECASE)
    if len(sectors) >= 2:
        ride_desc = f"Rapido: {sectors[0]} → {sectors[1]}"
    elif sectors:
        ride_desc = f"Rapido: {sectors[0]}"

    # Date: "Sep 1st 2026" — ordinal suffix stripped by _parse_date
    txn_date = date.today().isoformat()
    date_m = re.search(
        r"((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{1,2}(?:st|nd|rd|th)?\s*,?\s*\d{4})",
        body, re.IGNORECASE,
    )
    if date_m:
        txn_date = _parse_date(date_m.group(1))

    return ParsedTransaction(amount, ride_desc, txn_date, "rapido", subject)


def parse_zomato(email: dict) -> Optional[ParsedTransaction]:
    """
    Zomato sends HTML order confirmation emails.
      From:    noreply@zomato.com
      Subject: "Your order from Burger King is confirmed"
      Body:    contains restaurant name, items, and total amount paid
    """
    body    = email["body"]
    subject = email["subject"]

    # Skip marketing / promotional emails
    if re.search(r"offer|discount|coupon|cashback|promo|deal|sale", subject, re.IGNORECASE):
        return None

    # Total paid — Zomato shows "Bill Total ₹XXX" or "Amount Paid ₹XXX"
    total_m = re.search(
        r"(?:Bill\s+Total|Amount\s+Paid|Total\s+Amount|Grand\s+Total|Total)\s*[:\-]?\s*(?:₹|Rs\.?)\s*([\d,]+(?:\.\d{1,2})?)",
        body, re.IGNORECASE,
    )
    amount = float(total_m.group(1).replace(",", "")) if total_m else _parse_amount(body)
    if not amount:
        return None

    # Restaurant name from subject: "Your order from Burger King is confirmed"
    restaurant = "Zomato Order"
    rest_m = re.search(r"(?:order from|ordered from)\s+(.+?)(?:\s+is\s+|\s+has\s+|$)", subject, re.IGNORECASE)
    if rest_m:
        restaurant = rest_m.group(1).strip()

    txn_date = date.today().isoformat()
    date_m = re.search(r"(\w+ \d{1,2},?\s*\d{4}|\d{1,2} \w+ \d{4}|\d{4}-\d{2}-\d{2})", body)
    if date_m:
        txn_date = _parse_date(date_m.group(1))

    return ParsedTransaction(amount, restaurant, txn_date, "zomato", subject)


# ── Category guesser ──────────────────────────────────────────────────────────

_CATEGORY_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"zomato|swiggy|uber\s*eat|food|restaurant|cafe|dining", re.I), "Food"),
    (re.compile(r"ola|uber|rapido|metro|bus|petrol|fuel|irctc|railway|flight|airline", re.I), "Transport"),
    (re.compile(r"netflix|spotify|amazon prime|hotstar|zee5|game|cinema|movie", re.I), "Entertainment"),
    (re.compile(r"electricity|water|gas|broadband|wifi|internet|phone|mobile recharge", re.I), "Utilities"),
    (re.compile(r"apollo|medplus|pharma|hospital|clinic|doctor|health|insurance", re.I), "Health"),
    (re.compile(r"amazon|flipkart|myntra|nykaa|meesho|ajio|shopping|mall", re.I), "Shopping"),
    (re.compile(r"credit card|hdfc|icici|sbi|axis|kotak|cred payment", re.I), "Utilities"),
]


def guess_category(merchant: str) -> str:
    for pattern, category in _CATEGORY_RULES:
        if pattern.search(merchant):
            return category
    return "Other"


# ── Main importer ─────────────────────────────────────────────────────────────

APP_QUERIES = {
    "paytm":  "from:noreply@paytm.com subject:(transaction OR payment OR paid)",
    "gpay":   "from:gpay-noreply@google.com OR from:googleplay-noreply@google.com subject:(paid OR payment)",
    "cred":   "from:noreply@cred.club subject:(payment OR bill)",
    "uber":   "from:uber@uber.com OR from:noreply@uber.com subject:(trip OR receipt)",
    "rapido": "from:partner@rapido.bike OR from:no-reply@rapido.bike OR from:noreply@rapido.bike subject:(ride OR receipt OR summary OR invoice)",
    "zomato": "from:noreply@zomato.com subject:(order OR confirmed OR delivered)",
}

APP_PARSERS = {
    "paytm":  parse_paytm,
    "gpay":   parse_gpay,
    "cred":   parse_cred,
    "uber":   parse_uber,
    "rapido": parse_rapido,
    "zomato": parse_zomato,
}

# Category overrides per source (skips guess_category for known sources)
SOURCE_CATEGORY = {
    "uber":   "Transport",
    "rapido": "Transport",
    "zomato": "Food",
}


def import_transactions(
    tracker: ExpenseTracker,
    since_days: int = 30,
    dry_run: bool = False,
) -> list[Expense]:
    """
    Fetch and import transactions from PayTM, GPay, and CRED emails.

    Args:
        tracker:    ExpenseTracker instance to add expenses to.
        since_days: How many days back to scan (default 30).
        dry_run:    If True, parse but do not write to tracker.

    Returns:
        List of Expense objects that were (or would be) added.
    """
    service = get_gmail_service()
    date_filter = f"newer_than:{since_days}d"

    # Load already-imported email IDs to avoid duplicates
    seen_file = Path(".imported_email_ids.json")
    seen_ids: set[str] = set(json.loads(seen_file.read_text()) if seen_file.exists() else [])

    added: list[Expense] = []

    for app, base_query in APP_QUERIES.items():
        query = f"{base_query} {date_filter}"
        print(f"  Scanning {app.upper()} emails...")
        emails = fetch_emails(service, query)

        for email in emails:
            if email["id"] in seen_ids:
                continue

            parsed = APP_PARSERS[app](email)
            if not parsed:
                continue

            category = SOURCE_CATEGORY.get(app) or guess_category(parsed.merchant)
            tags = [app]

            print(
                f"    [{app.upper()}] {parsed.date}  ₹{parsed.amount:.2f}"
                f"  →  {parsed.merchant}  ({category})"
            )

            if not dry_run:
                expense = tracker.add_expense(
                    amount=parsed.amount,
                    category=category,
                    description=parsed.merchant,
                    date=parsed.date,
                    tags=tags,
                )
                added.append(expense)
                seen_ids.add(email["id"])

    if not dry_run:
        seen_file.write_text(json.dumps(list(seen_ids)))

    print(f"\n  {'Would import' if dry_run else 'Imported'} {len(added)} new transaction(s).")
    return added


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Import expenses from Gmail")
    parser.add_argument("--days", type=int, default=30, help="Days back to scan (default 30)")
    parser.add_argument("--dry-run", action="store_true", help="Parse only, don't save")
    args = parser.parse_args()

    tracker = ExpenseTracker()
    print(f"\nImporting last {args.days} days of transactions...\n")
    import_transactions(tracker, since_days=args.days, dry_run=args.dry_run)
