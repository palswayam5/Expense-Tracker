"""Parse Splitwise monthly balance email from Gmail.

Email format (from hello@splitwise.com, subject "Your balance for <month>"):
    Total balance
    you are owed INR985.50
    Kenneth Lobo
    you owe INR1,650.50
    Akshant
    owes you INR918.50
    ...
"""
import re
from datetime import date, timedelta

# Matches INR, Rs., ₹ followed by amount
_AMT = re.compile(r'(?:₹|inr\s*|rs\.?\s*)([\d,]+(?:\.\d+)?)', re.IGNORECASE)

_SKIP_LINES = {
    'total balance', 'splitwise', 'largest expenses', 'see all friends',
    'see all transactions', 'visit splitwise', 'review your spending',
    'have a great day', 'unsubscribe', 'p.s.', 'balance',
    'settle up', 'record a cash payment', 'the splitwise team',
    'you lent', 'you borrowed', 'paid to',
}


def _looks_like_name(s: str) -> bool:
    """True if s could be a person's name rather than a header or sentence."""
    s = s.strip()
    if len(s) < 2 or len(s) > 50:
        return False
    if any(kw in s.lower() for kw in _SKIP_LINES):
        return False
    # Names start with a letter and contain only word chars / spaces / dots
    if not re.match(r'^[A-Za-z][\w\s.\-]{1,49}$', s):
        return False
    # Sentences (many words) are not names
    if len(s.split()) > 4:
        return False
    return True


def _parse_email(text: str) -> tuple:
    """Return (net_balance, breakdown_dict) from raw email text.

    net_balance is None if not found.
    breakdown_dict: {name: signed_amount}  positive = they owe you.
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    net = None
    breakdown: dict[str, float] = {}

    for i, line in enumerate(lines):
        ll = line.lower()

        # ── Overall balance ──────────────────────────────────────────────────
        if 'you are owed' in ll:
            m = _AMT.search(line)
            if m and net is None:
                net = float(m.group(1).replace(',', ''))

        # ── "you owe INR X" → the previous line is the person's name
        #    i.e. YOU owe THEM → negative for you
        elif re.match(r'^you\s+owe\s+(?:₹|inr|rs)', ll):
            m = _AMT.search(line)
            if m and i > 0:
                name = lines[i - 1].strip()
                if _looks_like_name(name):
                    breakdown[name] = -float(m.group(1).replace(',', ''))

        # ── "owes you INR X" → the previous line is the person's name
        #    i.e. THEY owe YOU → positive for you
        elif re.match(r'^owes?\s+you\s+(?:₹|inr|rs)', ll):
            m = _AMT.search(line)
            if m and i > 0:
                name = lines[i - 1].strip()
                if _looks_like_name(name):
                    breakdown[name] = float(m.group(1).replace(',', ''))

    # Fallback: derive net from per-person totals
    if net is None and breakdown:
        net = round(sum(breakdown.values()), 2)

    return net, breakdown


def fetch_balance_from_gmail(since_days: int = 120) -> dict:
    """Fetch net Splitwise balance by parsing the monthly summary email from Gmail."""
    from email_importer import get_gmail_service, fetch_emails

    service = get_gmail_service()

    since_str = (date.today() - timedelta(days=since_days)).strftime('%Y/%m/%d')

    # Try both known Splitwise sender addresses
    emails = []
    for sender in ('hello@splitwise.com', 'notifications@splitwise.com'):
        q = f'from:{sender} after:{since_str}'
        found = fetch_emails(service, q, max_results=10)
        emails.extend(found)

    if not emails:
        return {
            'error': (
                'No Splitwise emails found in the last 4 months.\n'
                'Make sure email notifications are ON in Splitwise settings '
                '(splitwise.com → Account settings → Notifications).'
            ),
            'net_balance': 0.0,
            'breakdown': [],
        }

    # Prefer the most recent monthly summary ("Your balance for …")
    summary_emails = [e for e in emails if 'balance' in e['subject'].lower()]
    candidates = summary_emails if summary_emails else emails

    net = None
    breakdown: dict[str, float] = {}

    for email in candidates:
        n, bd = _parse_email(email['body'])
        if n is not None:
            net = n
            breakdown = bd
            break

    if net is None:
        return {
            'error': (
                f'Found {len(emails)} Splitwise email(s) but couldn\'t parse the balance. '
                'Try setting the balance manually below.'
            ),
            'net_balance': 0.0,
            'breakdown': [],
        }

    bd_list = [
        {'name': k, 'amount': round(v, 2)}
        for k, v in breakdown.items()
        if abs(v) >= 0.01
    ]
    return {
        'net_balance':  round(net, 2),
        'breakdown':    sorted(bd_list, key=lambda x: -abs(x['amount'])),
        'emails_found': len(emails),
    }
