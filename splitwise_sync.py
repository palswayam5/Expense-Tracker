"""Fetch net Splitwise balance from the Splitwise API using a personal API key."""
import json
import urllib.request
import urllib.error
import os

SPLITWISE_API_BASE = "https://secure.splitwise.com/api/v3.0"


def _get(api_key: str, path: str) -> dict:
    req = urllib.request.Request(
        f"{SPLITWISE_API_BASE}{path}",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise RuntimeError(f"Splitwise API {e.code}: {body[:200]}")


def fetch_balance(api_key: str) -> dict:
    """Return net INR balance and per-friend breakdown.
    Positive amount = they owe you. Negative = you owe them.
    """
    data = _get(api_key, "/get_friends")
    friends = data.get("friends", [])

    net = 0.0
    breakdown = []

    for f in friends:
        name = f"{f.get('first_name', '')} {f.get('last_name', '')}".strip() or "Unknown"
        for bal in f.get("balance", []):
            if bal.get("currency_code") == "INR":
                amount = float(bal.get("amount", 0))
                if amount != 0:
                    net += amount
                    breakdown.append({"name": name, "amount": round(amount, 2)})

    return {
        "net_balance": round(net, 2),
        "breakdown":   sorted(breakdown, key=lambda x: -abs(x["amount"])),
    }
