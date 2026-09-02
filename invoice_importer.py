
"""Parse Zepto (and similar quick-commerce) invoice PDFs and import into ExpenseTracker."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path


# ── Auto-categorisation ───────────────────────────────────────────────────────

_RULES: list[tuple[list[str], str]] = [
    (
        [
            "milk", "curd", "yogurt", "paneer", "butter", "ghee", "cheese", "cream",
            "bread", "egg", "rice", "atta", "flour", "dal", "pulse", "lentil",
            "oil", "mustard", "sunflower", "olive",
            "banana", "apple", "mango", "tomato", "onion", "potato", "vegetable", "fruit",
            "juice", "water", "tea", "coffee",
            "biscuit", "cookie", "chips", "namkeen", "snack", "chocolate",
            "noodle", "pasta", "sauce", "ketchup", "masala", "spice",
            "honey", "jam", "cereal", "oat", "muesli", "protein", "whey",
        ],
        "Food",
    ),
    (
        [
            "medicine", "tablet", "capsule", "syrup", "ointment", "bandage",
            "vitamin", "supplement", "probiotic",
            "toothpaste", "toothbrush", "floss", "mouthwash",
            "hand wash", "handwash", "sanitizer", "antiseptic",
            "painkiller", "antacid", "cough", "cold", "balm",
        ],
        "Health",
    ),
    (
        [
            "shampoo", "conditioner", "body wash", "soap", "shower gel",
            "lotion", "moisturiser", "moisturizer", "face wash", "sunscreen",
            "serum", "toner", "deodorant", "perfume", "razor", "shaving",
            "lipstick", "kajal", "makeup",
            "tissue", "wipe", "detergent", "dishwash", "cleaner",
            "container", "bottle", "box", "storage", "tape", "stationery",
        ],
        "Shopping",
    ),
]


def guess_category(name: str) -> str:
    low = name.lower()
    for keywords, cat in _RULES:
        if any(kw in low for kw in keywords):
            return cat
    return "Shopping"


# ── PDF helpers ───────────────────────────────────────────────────────────────

def _to_float(raw) -> float:
    cleaned = re.sub(r"[^\d.]", "", str(raw or "").strip())
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _parse_date(text: str) -> str:
    m = re.search(r"Date\s*[:\-]\s*(\d{1,2}[-/]\d{1,2}[-/]\d{4})", text)
    if m:
        for fmt in ("%d-%m-%Y", "%d/%m/%Y"):
            try:
                return datetime.strptime(m.group(1), fmt).strftime("%Y-%m-%d")
            except ValueError:
                pass
    return datetime.today().strftime("%Y-%m-%d")


def _parse_invoice_no(text: str) -> str:
    m = re.search(r"Invoice\s*No\.?\s*[:\-]?\s*([A-Z0-9]+)", text)
    return m.group(1) if m else ""


def _parse_vendor(text: str) -> str:
    m = re.search(r"Seller\s*Name\s*[:\-]?\s*(.+)", text)
    return m.group(1).strip() if m else ""


# ── Main parser ───────────────────────────────────────────────────────────────

def _extract_item_row(row: list) -> tuple[str, float, float] | None:
    """
    Return (name, qty, total_amount) from a Zepto GST invoice row, or None.

    Zepto page 1:  row[0]=SR(digit)  row[1]=desc  row[4]=qty  row[-1]=total
    Zepto page 2:  row[0]=None       row[1]=SR     row[2]=desc row[5]=qty  row[-1]=total
    (page 2 rows are shifted by one because pdfplumber merges the overflowed header)
    """
    if not row:
        return None

    cell0 = str(row[0] or "").strip()
    cell1 = str(row[1] or "").strip()

    if cell0.isdigit():
        # page-1 layout
        desc_col, qty_col = 1, 4
    elif not cell0 and cell1.isdigit():
        # page-2 layout (shifted right by one)
        desc_col, qty_col = 2, 5
    else:
        return None

    if len(row) <= max(desc_col, qty_col):
        return None

    raw_name = str(row[desc_col] or "").strip()
    name = " ".join(raw_name.splitlines())           # flatten multi-line
    name = re.sub(r"\s*\|.*$", "", name).strip()     # drop "| size info"
    name = re.sub(r"\s{2,}", " ", name)[:60]

    qty    = _to_float(row[qty_col])
    qty    = qty if qty > 0 else 1.0
    amount = _to_float(row[-1])

    if amount <= 0 or not name:
        return None

    return name, qty, amount


def parse_invoice(pdf_path: str | Path) -> dict:
    """
    Parse a quick-commerce invoice PDF (Zepto, Blinkit, Swiggy Instamart).

    Returns:
        date        : "YYYY-MM-DD"
        invoice_no  : str
        vendor      : str
        items       : list[{name, qty, unit_price, amount, category}]
                      amount = Total Amt. (tax-inclusive, what was paid)
        total       : float
    """
    try:
        import pdfplumber
    except ImportError:
        raise RuntimeError("pdfplumber is required:  pip install pdfplumber")

    items: list[dict] = []

    with pdfplumber.open(str(pdf_path)) as pdf:
        full_text = "\n".join(p.extract_text() or "" for p in pdf.pages)
        inv_date  = _parse_date(full_text)
        inv_no    = _parse_invoice_no(full_text)
        vendor    = _parse_vendor(full_text)

        for page in pdf.pages:
            for table in (page.extract_tables() or []):
                for row in table:
                    result = _extract_item_row(row)
                    if result is None:
                        continue
                    name, qty, amount = result
                    unit_price = round(amount / qty, 2)
                    items.append({
                        "name":       name,
                        "qty":        qty,
                        "unit_price": unit_price,
                        "amount":     amount,
                        "category":   guess_category(name),
                    })

    return {
        "date":       inv_date,
        "invoice_no": inv_no,
        "vendor":     vendor,
        "items":      items,
        "total":      round(sum(i["amount"] for i in items), 2),
    }


def import_invoice(pdf_path: str, tracker) -> list:
    """Parse a PDF invoice and import all items into the ExpenseTracker."""
    inv = parse_invoice(pdf_path)

    BOLD  = "\033[1m"
    GREEN = "\033[32m"
    RESET = "\033[0m"

    print(f"\n{BOLD}Invoice  {inv['invoice_no']}{RESET}  —  {inv['vendor']}")
    print(f"Date: {inv['date']}   Items: {len(inv['items'])}   Total: ₹{inv['total']:,.2f}\n")
    print(f"  {'#':<3} {'Item':<42} {'Qty':>4} {'₹':>8}  Category")
    print(f"  {'─'*66}")
    for i, item in enumerate(inv["items"], 1):
        print(f"  {i:<3} {item['name']:<42} {item['qty']:>4g} {item['amount']:>8.2f}  {item['category']}")
    print(f"  {'─'*66}")
    print(f"  {'Total':<49} ₹{inv['total']:>8,.2f}")

    confirm = input("\nImport all items? (y/n): ").strip().lower()
    if confirm != "y":
        print("Cancelled.")
        return []

    imported = []
    for item in inv["items"]:
        e = tracker.add_expense(
            amount=item["unit_price"],
            category=item["category"],
            description=item["name"],
            date=inv["date"],
            tags=["invoice", inv["vendor"].split()[0].lower()],
            quantity=item["qty"],
        )
        imported.append(e)

    print(f"\n{GREEN}Imported {len(imported)} items from {inv['vendor']}{RESET}")
    return imported
