"""Parse Splitwise balance from Gmail notification emails."""
import re
from datetime import date, timedelta

# ── Regex patterns ────────────────────────────────────────────────────────────

# "you are owed ₹1,234.56" or "overall, you are owed ₹X"
_OWED_TO_YOU = re.compile(
    r'you\s+are\s+owed\s+(?:₹|rs\.?\s*)([\d,]+(?:\.\d+)?)',
    re.IGNORECASE,
)

# "you owe ₹1,234.56" — currency symbol directly after "owe", so NO name between them
# This naturally excludes "you owe Rahul ₹X" because "Rahul" breaks the match
_YOU_OWE_NET = re.compile(
    r'you\s+owe(?:\s+a?\s*total\s+of)?\s+(?:₹|rs\.?\s*)([\d,]+(?:\.\d+)?)',
    re.IGNORECASE,
)

# "[Name] owes you ₹X"
_PERSON_OWES = re.compile(
    r'([A-Z][a-zA-Z]{1,20}(?:\s+[A-Z][a-zA-Z]{1,20})?)\s+owes?\s+you\s+(?:₹|rs\.?\s*)([\d,]+(?:\.\d+)?)',
    re.IGNORECASE,
)

# "you owe [Name] ₹X"
_YOU_OWE_NAME = re.compile(
    r'you\s+owe\s+([A-Z][a-zA-Z]{1,20}(?:\s+[A-Z][a-zA-Z]{1,20})?)\s+(?:₹|rs\.?\s*)([\d,]+(?:\.\d+)?)',
    re.IGNORECASE,
)


def fetch_balance_from_gmail(since_days: int = 90) -> dict:
    """Fetch net Splitwise balance by parsing Gmail notification emails.

    Returns {"net_balance": float, "breakdown": [...], "emails_found": int}
    or {"error": str, "net_balance": 0.0, "breakdown": []}
    """
    from email_importer import get_gmail_service, fetch_emails

    service = get_gmail_service()

    since_str = (date.today() - timedelta(days=since_days)).strftime("%Y/%m/%d")
    query = f"from:notifications@splitwise.com after:{since_str}"

    emails = fetch_emails(service, query, max_results=40)
    if not emails:
        return {
            "error": (
                "No Splitwise emails found in the last 90 days.\n"
                "Make sure email notifications are ON in Splitwise settings "
                "(splitwise.com → Account settings → Notifications)."
            ),
            "net_balance": 0.0,
            "breakdown": [],
        }

    net_balance = None
    breakdown: dict[str, float] = {}

    # Process newest-first; grab overall balance from the first email that has it
    for email in emails:
        body = email["body"]

        # Overall balance: "you are owed ₹X"
        m = _OWED_TO_YOU.search(body)
        if m and net_balance is None:
            net_balance = float(m.group(1).replace(",", ""))

        # Overall balance: "you owe ₹X" (no name between owe and ₹)
        m = _YOU_OWE_NET.search(body)
        if m and net_balance is None:
            net_balance = -float(m.group(1).replace(",", ""))

        # Per-person: "[Name] owes you ₹X"
        for m in _PERSON_OWES.finditer(body):
            name = m.group(1).strip().title()
            amt  = float(m.group(2).replace(",", ""))
            if name.lower() not in ("nobody", "no one", "everyone", "someone"):
                breakdown[name] = max(breakdown.get(name, 0.0), amt)

        # Per-person: "you owe [Name] ₹X"
        for m in _YOU_OWE_NAME.finditer(body):
            name = m.group(1).strip().title()
            amt  = float(m.group(2).replace(",", ""))
            if name.lower() not in ("nobody", "no one", "everyone", "someone"):
                breakdown[name] = min(breakdown.get(name, 0.0), -amt)

        if net_balance is not None:
            break  # found overall balance; stop scanning

    # Fallback: compute net from per-person data
    if net_balance is None and breakdown:
        net_balance = round(sum(breakdown.values()), 2)

    if net_balance is None:
        return {
            "error": (
                f"Found {len(emails)} Splitwise email(s) but couldn't parse the balance. "
                "The email may be in a different format — try setting the balance manually below."
            ),
            "net_balance": 0.0,
            "breakdown": [],
        }

    bd_list = [
        {"name": k, "amount": round(v, 2)}
        for k, v in breakdown.items()
        if abs(v) >= 0.01
    ]
    return {
        "net_balance":   round(net_balance, 2),
        "breakdown":     sorted(bd_list, key=lambda x: -abs(x["amount"])),
        "emails_found":  len(emails),
    }
