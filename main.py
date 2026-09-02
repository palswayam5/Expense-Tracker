import json
import os
import uuid
from datetime import datetime, date
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional
from collections import defaultdict


DATA_FILE = Path("expenses.json")


@dataclass
class Category:
    name: str
    monthly_budget: float = 0.0   # 0 = no limit; only applies to expense categories
    entry_type: str = "expense"   # "expense" | "income"


PAYMENT_METHODS = [
    "GPay", "PayTM", "PhonePe", "CRED",
    "Credit Card", "Debit Card", "Cash", "Net Banking",
]

@dataclass
class Expense:
    amount: float        # unit price
    category: str
    description: str
    date: str = field(default_factory=lambda: date.today().isoformat())
    tags: list[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    entry_type: str = "expense"   # "expense" | "income"
    quantity: float = 1.0
    payment_method: str = ""

    @property
    def total(self) -> float:
        return round(self.amount * self.quantity, 2)


DEFAULT_CATEGORIES = [
    # expense categories
    Category("Food",            500.0,  "expense"),
    Category("Transport",       200.0,  "expense"),
    Category("Entertainment",   150.0,  "expense"),
    Category("Utilities",       300.0,  "expense"),
    Category("Health",          200.0,  "expense"),
    Category("Shopping",        400.0,  "expense"),
    Category("Family/Home",       0.0,  "expense"),
    Category("Loan Repayment",    0.0,  "expense"),
    Category("Other",             0.0,  "expense"),
    # income categories
    Category("Salary",            0.0,  "income"),
    Category("Freelance",         0.0,  "income"),
    Category("Other Income",      0.0,  "income"),
]


class ExpenseTracker:
    def __init__(self, data_file: Path = DATA_FILE):
        self.data_file = data_file
        self.expenses: list[Expense] = []
        self.categories: dict[str, Category] = {
            c.name: c for c in DEFAULT_CATEGORIES
        }
        self.opening_balance: float = 0.0
        self._load()

    # ── Persistence ──────────────────────────────────────────────────────────

    def _parse_raw(self, raw: dict):
        self.expenses = [
            Expense(**{**e,
                       "entry_type":      e.get("entry_type", "expense"),
                       "quantity":        e.get("quantity", 1.0),
                       "payment_method":  e.get("payment_method", "")})
            for e in raw.get("expenses", [])
        ]
        for c in raw.get("categories", []):
            self.categories[c["name"]] = Category(**{**c, "entry_type": c.get("entry_type", "expense")})
        self.opening_balance = raw.get("opening_balance", 0.0)

    def _build_data(self) -> dict:
        return {
            "expenses":        [asdict(e) for e in self.expenses],
            "categories":      [asdict(c) for c in self.categories.values()],
            "opening_balance": self.opening_balance,
        }

    def _load(self):
        if os.environ.get("DATABASE_URL"):
            self._load_from_db(os.environ["DATABASE_URL"])
        elif self.data_file.exists():
            self._parse_raw(json.loads(self.data_file.read_text()))

    def _save(self):
        if os.environ.get("DATABASE_URL"):
            self._save_to_db(os.environ["DATABASE_URL"])
        else:
            self.data_file.write_text(json.dumps(self._build_data(), indent=2))

    def _load_from_db(self, db_url: str):
        try:
            import psycopg2
            with psycopg2.connect(db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS tracker_data (
                            id INTEGER PRIMARY KEY,
                            data JSONB NOT NULL
                        )
                    """)
                    conn.commit()
                    cur.execute("SELECT data FROM tracker_data WHERE id = 1")
                    row = cur.fetchone()
                    if row:
                        self._parse_raw(row[0])
        except Exception as e:
            print(f"[DB] Load error: {e}")

    def _save_to_db(self, db_url: str):
        try:
            import psycopg2
            with psycopg2.connect(db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS tracker_data (
                            id INTEGER PRIMARY KEY,
                            data JSONB NOT NULL
                        )
                    """)
                    cur.execute("""
                        INSERT INTO tracker_data (id, data) VALUES (1, %s)
                        ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data
                    """, (json.dumps(self._build_data()),))
                conn.commit()
        except Exception as e:
            print(f"[DB] Save error: {e}")

    # ── Categories ───────────────────────────────────────────────────────────

    def add_category(self, name: str, monthly_budget: float = 0.0) -> Category:
        cat = Category(name=name, monthly_budget=monthly_budget)
        self.categories[name] = cat
        self._save()
        return cat

    def set_budget(self, category: str, monthly_budget: float):
        if category not in self.categories:
            raise ValueError(f"Category '{category}' not found.")
        self.categories[category].monthly_budget = monthly_budget
        self._save()

    # ── Expenses ─────────────────────────────────────────────────────────────

    def add_expense(
        self,
        amount: float,
        category: str,
        description: str,
        date: Optional[str] = None,
        tags: Optional[list[str]] = None,
        entry_type: str = "expense",
        quantity: float = 1.0,
        payment_method: str = "",
    ) -> Expense:
        if category not in self.categories:
            raise ValueError(f"Category '{category}' not found. Add it first.")
        expense = Expense(
            amount=round(amount, 2),
            category=category,
            description=description,
            date=date or datetime.today().date().isoformat(),
            tags=tags or [],
            entry_type=entry_type,
            quantity=quantity,
            payment_method=payment_method,
        )
        self.expenses.append(expense)
        self._save()
        return expense

    def increment_quantity(self, expense_id: str, by: float = 1.0) -> Expense:
        expense = self._get_by_id(expense_id)
        expense.quantity = round(expense.quantity + by, 3)
        self._save()
        return expense

    def find_by_name(self, name: str, category: str, month: str) -> Optional["Expense"]:
        """Find an existing entry matching name+category in a given month (case-insensitive)."""
        needle = name.strip().lower()
        for e in self.get_expenses(category=category, month=month):
            if e.description.lower() == needle:
                return e
        return None

    def list_categories(self, entry_type: Optional[str] = None) -> list[Category]:
        cats = list(self.categories.values())
        if entry_type:
            cats = [c for c in cats if c.entry_type == entry_type]
        return cats

    def edit_expense(self, expense_id: str, **kwargs) -> Expense:
        expense = self._get_by_id(expense_id)
        for key, value in kwargs.items():
            if not hasattr(expense, key) or key == "id":
                raise ValueError(f"Invalid field: {key}")
            setattr(expense, key, value)
        self._save()
        return expense

    def delete_expense(self, expense_id: str):
        expense = self._get_by_id(expense_id)
        self.expenses.remove(expense)
        self._save()

    def _get_by_id(self, expense_id: str) -> Expense:
        for e in self.expenses:
            if e.id == expense_id:
                return e
        raise ValueError(f"Expense '{expense_id}' not found.")

    # ── Querying ─────────────────────────────────────────────────────────────

    def get_expenses(
        self,
        category: Optional[str] = None,
        month: Optional[str] = None,   # "YYYY-MM"
        tag: Optional[str] = None,
    ) -> list[Expense]:
        results = self.expenses
        if category:
            results = [e for e in results if e.category == category]
        if month:
            results = [e for e in results if e.date.startswith(month)]
        if tag:
            results = [e for e in results if tag in e.tags]
        return sorted(results, key=lambda e: e.date, reverse=True)

    # ── Email import ─────────────────────────────────────────────────────────

    def sync_from_gmail(self, since_days: int = 30, dry_run: bool = False):
        """Pull transactions from PayTM, GPay, and CRED emails via Gmail API."""
        from email_importer import import_transactions
        return import_transactions(self, since_days=since_days, dry_run=dry_run)

    # ── Summaries ────────────────────────────────────────────────────────────

    def monthly_summary(self, month: Optional[str] = None) -> dict:
        month = month or date.today().strftime("%Y-%m")
        entries = self.get_expenses(month=month)

        income_totals: dict[str, float] = defaultdict(float)
        expense_totals: dict[str, float] = defaultdict(float)
        for e in entries:
            if e.entry_type == "income":
                income_totals[e.category] += e.total
            else:
                expense_totals[e.category] += e.total

        total_income  = round(sum(income_totals.values()), 2)
        total_expense = round(sum(expense_totals.values()), 2)
        return {
            "month": month,
            "total_income":  total_income,
            "total_expense": total_expense,
            "net":           round(total_income - total_expense, 2),
            "income_by_category":  {k: round(v, 2) for k, v in sorted(income_totals.items())},
            "expense_by_category": {k: round(v, 2) for k, v in sorted(expense_totals.items())},
        }

    def budget_status(self, month: Optional[str] = None) -> list[dict]:
        summary = self.monthly_summary(month)
        results = []
        for cat in self.categories.values():
            if cat.entry_type != "expense" or cat.monthly_budget <= 0:
                continue
            spent = summary["expense_by_category"].get(cat.name, 0.0)
            over  = spent > cat.monthly_budget
            results.append({
                "category":   cat.name,
                "budget":     cat.monthly_budget,
                "spent":      spent,
                "remaining":  round(cat.monthly_budget - spent, 2),
                "over_budget": over,
            })
        return results

    def all_time_summary(self) -> dict:
        total_income  = round(sum(e.total for e in self.expenses if e.entry_type == "income"), 2)
        total_expense = round(sum(e.total for e in self.expenses if e.entry_type == "expense"), 2)
        months_active = len({e.date[:7] for e in self.expenses})
        return {
            "total_income":   total_income,
            "total_expense":  total_expense,
            "net_saved":      round(total_income - total_expense, 2),
            "months_active":  months_active,
        }

    def set_opening_balance(self, amount: float):
        self.opening_balance = round(amount, 2)
        self._save()

    def balance_by_payment_method(self) -> dict[str, float]:
        """Total spent via each payment method (expenses only)."""
        totals: dict[str, float] = defaultdict(float)
        for e in self.expenses:
            if e.entry_type == "expense":
                key = e.payment_method or "Untracked"
                totals[key] += e.total
        return {k: round(v, 2) for k, v in sorted(totals.items(), key=lambda x: -x[1])}

    def spending_by_tag(self, month: Optional[str] = None) -> dict[str, float]:
        month = month or date.today().strftime("%Y-%m")
        totals: dict[str, float] = defaultdict(float)
        for e in self.get_expenses(month=month):
            for tag in e.tags:
                totals[tag] += e.total
        return {k: round(v, 2) for k, v in sorted(totals.items())}


# ── CLI ───────────────────────────────────────────────────────────────────────

def _pick(prompt: str, options: list[str]) -> str:
    for i, opt in enumerate(options, 1):
        print(f"  {i}. {opt}")
    while True:
        raw = input(prompt).strip()
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]
        print("  Invalid choice, try again.")


def _ask(prompt: str, default: str = "") -> str:
    val = input(prompt).strip()
    return val if val else default


def _ask_payment_method() -> str:
    print("  Paid via:")
    for i, m in enumerate(PAYMENT_METHODS, 1):
        print(f"    {i}. {m}")
    raw = input(f"  Choice [Enter to skip]: ").strip()
    if raw.isdigit() and 1 <= int(raw) <= len(PAYMENT_METHODS):
        return PAYMENT_METHODS[int(raw) - 1]
    return ""


def _ask_date() -> str:
    import curses
    import calendar as cal_mod
    from datetime import timedelta

    print("  Date:  Enter = calendar   m = type manually")
    mode = input("  > ").strip().lower() or "c"
    if mode == "m":
        while True:
            raw = input("  Enter date (YYYY-MM-DD): ").strip()
            if not raw:
                return date.today().isoformat()
            try:
                datetime.strptime(raw, "%Y-%m-%d")
                return raw
            except ValueError:
                print("  Invalid format, use YYYY-MM-DD.")

    result: list[Optional[str]] = [None]

    def _picker(stdscr):
        curses.curs_set(0)
        cur = date.today()

        while True:
            stdscr.clear()
            year, month = cur.year, cur.month

            # ── header ──
            title = f"  {cal_mod.month_name[month]}  {year}"
            stdscr.addstr(0, 0, title, curses.A_BOLD)
            stdscr.addstr(0, 26, "< prev month   next month >", curses.A_DIM)
            stdscr.addstr(1, 0, "  arrows: navigate   Enter: select   Esc: cancel", curses.A_DIM)

            # ── weekday row ──
            stdscr.addstr(3, 2, " Mo  Tu  We  Th  Fr  Sa  Su")

            # ── day grid ──
            today = date.today()
            for row, week in enumerate(cal_mod.monthcalendar(year, month)):
                for col, day in enumerate(week):
                    if day == 0:
                        continue
                    d = date(year, month, day)
                    x = 2 + col * 4
                    y = 4 + row
                    if d == cur:
                        attr = curses.A_REVERSE | curses.A_BOLD
                    elif d == today:
                        attr = curses.A_UNDERLINE
                    else:
                        attr = curses.A_NORMAL
                    stdscr.addstr(y, x, f"{day:2} ", attr)

            stdscr.addstr(11, 0, f"  Selected: {cur.isoformat()}", curses.A_BOLD)
            stdscr.refresh()

            key = stdscr.getch()

            if key == 27:                              # Esc — cancel
                break
            elif key in (ord('\n'), ord('\r'), curses.KEY_ENTER):
                result[0] = cur.isoformat()
                break
            elif key == curses.KEY_LEFT:
                cur -= timedelta(days=1)
            elif key == curses.KEY_RIGHT:
                cur += timedelta(days=1)
            elif key == curses.KEY_UP:
                cur -= timedelta(weeks=1)
            elif key == curses.KEY_DOWN:
                cur += timedelta(weeks=1)
            elif key == ord(','):                      # , — previous month
                first = cur.replace(day=1)
                cur = (first - timedelta(days=1)).replace(day=min(cur.day, cal_mod.monthrange(
                    *((first - timedelta(days=1)).year, (first - timedelta(days=1)).month))[1]))
            elif key == ord('.'):                      # . — next month
                last = cur.replace(day=cal_mod.monthrange(year, month)[1])
                nxt = last + timedelta(days=1)
                cur = nxt.replace(day=min(cur.day, cal_mod.monthrange(nxt.year, nxt.month)[1]))

    curses.wrapper(_picker)
    # curses clears screen on exit — reprint the chosen date
    chosen = result[0] or date.today().isoformat()
    print(f"  Date: {chosen}")
    return chosen


CATEGORY_SUGGESTIONS: dict[str, list[str]] = {
    "Transport":     ["Bike", "Auto", "E-rickshaw", "Cab (Ola/Uber)", "Metro", "Bus", "Train/IRCTC", "Petrol/Fuel"],
    "Utilities":     ["Electricity", "Water", "Gas", "Rent", "Furniture/Appliance rent", "Broadband/WiFi", "Mobile recharge"],
    "Food":          ["Groceries", "Restaurant", "Zomato/Swiggy", "Cafe/Coffee", "Snacks"],
    "Entertainment":  ["Movies/OTT", "Gaming", "Concerts/Events", "Sports"],
    "Health":         ["Medicine", "Doctor visit", "Lab test", "Gym/Fitness", "Insurance premium"],
    "Shopping":       ["Clothes", "Electronics", "Books", "Home decor", "Personal care"],
    "Family/Home":    ["Money sent home", "Groceries for family", "House maintenance", "Family event"],
    "Loan Repayment": ["Home loan EMI", "Car loan EMI", "Personal loan EMI", "Education loan EMI", "Credit card due"],
    "Salary":         ["Monthly salary", "Bonus", "Arrears"],
    "Freelance":      ["Project payment", "Consulting fee", "Part-time work"],
}


def _ask_description(category: str) -> str:
    suggestions = CATEGORY_SUGGESTIONS.get(category)
    if not suggestions:
        return _ask("Description: ")

    print(f"  {category} — pick a type or choose Other to type:")
    options = suggestions + ["Other (type manually)"]
    for i, opt in enumerate(options, 1):
        print(f"    {i:>2}. {opt}")

    while True:
        raw = input("  Choice: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            picked = options[int(raw) - 1]
            if picked == "Other (type manually)":
                return _ask("  Description: ")
            # Let them append a note (e.g. "Cab (Ola/Uber) — airport")
            note = _ask(f"  Add a note to '{picked}' (optional): ")
            return f"{picked} — {note}" if note else picked
        print("  Invalid choice, try again.")


def cmd_add(tracker: ExpenseTracker):
    print("\n  1. Expense\n  2. Income")
    entry_type = "expense" if _ask("Type [1]: ", "1") != "2" else "income"

    print(f"\n{'Income' if entry_type == 'income' else 'Expense'} categories:")
    cats = [c.name for c in tracker.list_categories(entry_type=entry_type)]
    category = _pick("Pick a category: ", cats)

    amount_raw = _ask("Amount (₹): ")
    try:
        amount = float(amount_raw)
    except ValueError:
        print("Invalid amount.")
        return

    description    = _ask_description(category)
    date_raw       = _ask_date()
    payment_method = _ask_payment_method() if entry_type == "expense" else ""
    tags_raw       = _ask("Tags (comma-separated, optional): ")
    tags           = [t.strip() for t in tags_raw.split(",") if t.strip()]

    entry = tracker.add_expense(amount, category, description, date_raw, tags,
                                entry_type, payment_method=payment_method)
    label = "Income" if entry_type == "income" else "Expense"
    pay   = f"  via {entry.payment_method}" if entry.payment_method else ""
    print(f"\nSaved [{label}]  [{entry.id}]  ₹{entry.total:.2f}  {entry.category}  — {entry.description}{pay}")


def cmd_list(tracker: ExpenseTracker):
    expenses = tracker.get_expenses()
    if not expenses:
        print("No entries yet.")
        return
    print(f"\n{'ID':<10} {'Type':<5} {'Date':<12} {'Category':<18} {'Qty':>5} {'Total ₹':>9} {'Via':<14}  Description")
    print("-" * 90)
    for e in expenses:
        label   = "INC" if e.entry_type == "income" else "EXP"
        qty_str = f"{e.quantity:g}"
        pay     = e.payment_method or "—"
        print(f"{e.id:<10} {label:<5} {e.date:<12} {e.category:<18} {qty_str:>5} {e.total:>9.2f} {pay:<14}  {e.description}")


def cmd_delete(tracker: ExpenseTracker):
    expenses = tracker.get_expenses()
    if not expenses:
        print("No expenses to delete.")
        return

    selected: set[int] = set()

    while True:
        print()
        for i, e in enumerate(expenses, 1):
            tick = "[x]" if i in selected else "[ ]"
            print(f"  {tick} {i:>2}. {e.date}  {e.category:<16} ₹{e.amount:>7.2f}  {e.description}")

        print("\n  Type numbers to toggle (e.g. 1 3 5), 'd' to delete selected, blank to cancel.")
        raw = input("  > ").strip()

        if not raw:
            return
        if raw.lower() == "d":
            break

        for token in raw.replace(",", " ").split():
            if token.isdigit() and 1 <= int(token) <= len(expenses):
                n = int(token)
                selected ^= {n}  # toggle on/off

    if not selected:
        print("Nothing selected.")
        return

    print("\nWill delete:")
    for i in sorted(selected):
        e = expenses[i - 1]
        print(f"  - ₹{e.amount:.2f}  {e.category}  {e.description}")

    confirm = _ask(f"\nDelete {len(selected)} expense(s)? (y/n): ")
    if confirm.lower() == "y":
        for i in sorted(selected, reverse=True):
            tracker.delete_expense(expenses[i - 1].id)
        print(f"Deleted {len(selected)} expense(s).")


def cmd_summary(tracker: ExpenseTracker):
    import calendar as cal
    from datetime import timedelta

    # ── ANSI helpers ──
    BOLD   = "\033[1m"
    GREEN  = "\033[32m"
    YELLOW = "\033[33m"
    RED    = "\033[31m"
    DIM    = "\033[2m"
    RESET  = "\033[0m"

    def bar(pct: float, width: int = 18) -> str:
        capped   = min(pct, 100)
        filled   = round(capped / 100 * width)
        color    = GREEN if pct < 70 else YELLOW if pct < 90 else RED
        return f"{color}{'█' * filled}{'░' * (width - filled)}{RESET}"

    def mom_arrow(cur: float, prev: float) -> str:
        if prev == 0:
            return f"{GREEN}  new{RESET}" if cur else ""
        delta = (cur - prev) / prev * 100
        if   delta >  20: return f"{RED}  ↑↑ +{delta:.0f}%{RESET}"
        elif delta >   0: return f"{YELLOW}  ↑ +{delta:.0f}%{RESET}"
        elif delta < -20: return f"{GREEN}  ↓↓ {delta:.0f}%{RESET}"
        elif delta <   0: return f"{GREEN}  ↓ {delta:.0f}%{RESET}"
        return ""

    today = date.today()

    # ── Month picker ──────────────────────────────────────────────────────────
    active_months = sorted({e.date[:7] for e in tracker.expenses}, reverse=True)
    if not active_months:
        active_months = [today.strftime("%Y-%m")]

    print("\n  Which month?")
    for i, m in enumerate(active_months, 1):
        suffix = "  ← current" if m == today.strftime("%Y-%m") else ""
        print(f"    {i}. {m}{suffix}")
    raw = input("  Choice [1]: ").strip() or "1"
    if raw.isdigit() and 1 <= int(raw) <= len(active_months):
        cur_month = active_months[int(raw) - 1]
    else:
        cur_month = active_months[0]

    # days context — only meaningful for the current month
    sel_year, sel_mon  = int(cur_month[:4]), int(cur_month[5:])
    days_in_month      = cal.monthrange(sel_year, sel_mon)[1]
    is_current         = cur_month == today.strftime("%Y-%m")
    days_elapsed       = today.day if is_current else days_in_month
    days_left          = (days_in_month - today.day) if is_current else 0

    first      = date(sel_year, sel_mon, 1)
    prev_month = (first - timedelta(days=1)).strftime("%Y-%m")

    summary    = tracker.monthly_summary(cur_month)
    prev       = tracker.monthly_summary(prev_month)
    status     = tracker.budget_status(cur_month)
    all_time   = tracker.all_time_summary()
    budget_map = {s["category"]: s for s in status}

    total_exp  = summary["total_expense"]
    total_inc  = summary["total_income"]
    daily_avg  = total_exp / days_elapsed if days_elapsed else 0
    projected  = daily_avg * days_in_month
    savings_pct = (summary["net"] / total_inc * 100) if total_inc else 0

    # ── All-time savings banner ───────────────────────────────────────────────
    net_saved  = all_time["net_saved"]
    save_color = GREEN if net_saved >= 0 else RED
    sign       = "+" if net_saved >= 0 else ""
    print(f"\n{save_color}{BOLD}{'▓'*52}{RESET}")
    print(f"{save_color}{BOLD}  TOTAL SAVED  {sign}₹{net_saved:>12,.2f}{RESET}"
          f"  {DIM}across {all_time['months_active']} month(s){RESET}")
    print(f"{DIM}  earned ₹{all_time['total_income']:,.2f}  —  spent ₹{all_time['total_expense']:,.2f}{RESET}")
    print(f"{save_color}{BOLD}{'▓'*52}{RESET}")

    # ── Monthly header ────────────────────────────────────────────────────────
    print(f"\n{BOLD}{'═'*52}{RESET}")
    day_info = f"day {days_elapsed}/{days_in_month}  ({days_left} days left)" if is_current else f"full month"
    print(f"  {BOLD}{cal.month_name[sel_mon]} {sel_year}{RESET}"
          f"  {DIM}{day_info}{RESET}")
    print(f"{'═'*52}")

    # ── Income & net ─────────────────────────────────────────────────────────
    if total_inc:
        print(f"  Income      {GREEN}{BOLD}₹{total_inc:>10,.2f}{RESET}")
    print(    f"  Expenses        ₹{total_exp:>10,.2f}")
    net  = summary["net"]
    sign = "+" if net >= 0 else ""
    net_color = GREEN if net >= 0 else RED
    print(    f"  Net         {net_color}{BOLD}{sign}₹{net:>10,.2f}{RESET}", end="")
    if total_inc:
        sr_color = GREEN if savings_pct >= 20 else YELLOW if savings_pct >= 0 else RED
        print(f"  {sr_color}savings {savings_pct:.0f}%{RESET}", end="")
    print()

    # ── Daily pace ───────────────────────────────────────────────────────────
    print(f"\n  Avg daily spend  ₹{daily_avg:,.0f}"
          f"  →  projected month total  ₹{projected:,.0f}")

    # ── Budget health ─────────────────────────────────────────────────────────
    print(f"\n  {BOLD}Budget Health{RESET}  {DIM}(█ spent  ░ remaining){RESET}")
    print(f"  {'─'*50}")
    for cat, spent in sorted(summary["expense_by_category"].items()):
        b = budget_map.get(cat)
        if b:
            pct  = spent / b["budget"] * 100
            proj = daily_avg and (spent / days_elapsed * days_in_month) if days_elapsed else spent
            over_proj = proj > b["budget"]
            proj_str  = (f"  {RED}→ proj ₹{proj:,.0f}{RESET}" if over_proj else "")
            print(f"  {cat:<16} {bar(pct)} {pct:>4.0f}%  "
                  f"₹{spent:,.0f}/₹{b['budget']:,.0f}{proj_str}")
        else:
            print(f"  {cat:<16} {DIM}₹{spent:,.2f}  (no budget set){RESET}")

    # ── Month-over-month ─────────────────────────────────────────────────────
    prev_exp = prev["expense_by_category"]
    if any(prev_exp.values()):
        prev_mon_name = cal.month_name[int(prev_month[5:])]
        print(f"\n  {BOLD}vs {prev_mon_name}{RESET}")
        print(f"  {'─'*50}")
        all_cats = sorted(set(summary["expense_by_category"]) | set(prev_exp))
        for cat in all_cats:
            cur_amt  = summary["expense_by_category"].get(cat, 0)
            prev_amt = prev_exp.get(cat, 0)
            delta    = cur_amt - prev_amt
            sign     = "+" if delta >= 0 else ""
            arrow    = mom_arrow(cur_amt, prev_amt)
            print(f"  {cat:<16} ₹{cur_amt:>8,.2f}  {DIM}({sign}₹{delta:,.0f} vs ₹{prev_amt:,.0f}){RESET}{arrow}")

    print(f"\n{'═'*52}\n")


def cmd_budget(tracker: ExpenseTracker):
    cats = tracker.list_categories(entry_type="expense")
    print()
    for i, c in enumerate(cats, 1):
        budget_str = f"₹{c.monthly_budget:.2f}" if c.monthly_budget else "no limit"
        print(f"  {i:>2}. {c.name:<16} {budget_str}")

    raw = _ask("\nPick category number (blank to cancel): ")
    if not raw or not raw.isdigit() or not (1 <= int(raw) <= len(cats)):
        return

    cat = cats[int(raw) - 1]
    current = f"₹{cat.monthly_budget:.2f}" if cat.monthly_budget else "no limit"
    new_raw = _ask(f"New monthly budget for {cat.name} (current: {current}, 0 = no limit): ")
    try:
        tracker.set_budget(cat.name, float(new_raw))
        print(f"Budget for {cat.name} updated to ₹{float(new_raw):.2f}.")
    except ValueError:
        print("Invalid amount.")


def cmd_edit(tracker: ExpenseTracker):
    expenses = tracker.get_expenses()
    if not expenses:
        print("No expenses to edit.")
        return

    print()
    for i, e in enumerate(expenses, 1):
        print(f"  {i:>2}. {e.date}  {e.category:<16} ₹{e.amount:>7.2f}  {e.description}")

    raw = _ask("\nPick number to edit (blank to cancel): ")
    if not raw or not raw.isdigit() or not (1 <= int(raw) <= len(expenses)):
        return

    e = expenses[int(raw) - 1]
    print(f"\nEditing: {e.description}  |  Press Enter to keep current value.\n")

    # Amount
    new_amt = _ask(f"  Amount (₹)  [{e.amount}]: ")
    if new_amt:
        try:
            e.amount = round(float(new_amt), 2)
        except ValueError:
            print("  Invalid amount, keeping original.")

    # Category
    cats = [c.name for c in tracker.list_categories()]
    print(f"  Category  [{e.category}]:")
    for i, c in enumerate(cats, 1):
        marker = ">" if c == e.category else " "
        print(f"    {marker} {i}. {c}")
    cat_raw = _ask("  Pick number (Enter to keep): ")
    if cat_raw.isdigit() and 1 <= int(cat_raw) <= len(cats):
        e.category = cats[int(cat_raw) - 1]

    # Description
    new_desc = _ask_description(e.category) if _ask(f"  Change description? (current: '{e.description}') y/n [n]: ").lower() == "y" else ""
    if new_desc:
        e.description = new_desc

    # Date
    if _ask(f"  Change date? (current: {e.date}) y/n [n]: ").lower() == "y":
        e.date = _ask_date()

    # Tags
    tags_display = ", ".join(e.tags) if e.tags else "none"
    new_tags = _ask(f"  Tags [{tags_display}] (comma-separated, Enter to keep): ")
    if new_tags:
        e.tags = [t.strip() for t in new_tags.split(",") if t.strip()]

    tracker.edit_expense(e.id, amount=e.amount, category=e.category,
                         description=e.description, date=e.date, tags=e.tags)
    print(f"\nUpdated  ₹{e.amount:.2f}  {e.category}  — {e.description}")





def cmd_import_invoice(tracker: ExpenseTracker):
    """Parse a Zepto/Blinkit/Instamart invoice PDF and bulk-add items."""
    GREEN  = "\033[32m"
    YELLOW = "\033[33m"
    DIM    = "\033[2m"
    BOLD   = "\033[1m"
    RESET  = "\033[0m"

    pdf_path = _ask("Path to invoice PDF: ").strip('"').strip("'")
    if not pdf_path or not Path(pdf_path).exists():
        print("  File not found.")
        return

    print("  Parsing invoice…")
    try:
        from invoice_importer import parse_invoice
        inv = parse_invoice(pdf_path)
    except RuntimeError as e:
        print(f"  {e}")
        return
    except Exception as e:
        print(f"  Failed to parse: {e}")
        return

    if not inv["items"]:
        print("  No items extracted — PDF layout may not be supported.")
        return

    cats = [c.name for c in tracker.list_categories(entry_type="expense")]

    print(f"\n  Invoice {inv['invoice_no']}  |  {inv['date']}  |  ₹{inv['total']:.2f} total")
    print(f"\n  {BOLD}{'#':<4} {'Item':<36} {'Amt ₹':>8}  Category{RESET}")
    print(f"  {'─'*64}")
    for i, item in enumerate(inv["items"], 1):
        print(f"  {i:<4} {item['name']:<36} {item['amount']:>8.2f}  {YELLOW}{item['category']}{RESET}")

    print(f"\n  {DIM}To fix a category: type its number and new category — e.g.  3 Health")
    print(f"  Multiple changes: one per line. Press Enter when done.{RESET}")

    while True:
        raw = input("  > ").strip()
        if not raw:
            break
        parts = raw.split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            idx, new_cat = int(parts[0]) - 1, parts[1].strip()
            if not (0 <= idx < len(inv["items"])):
                print("  Invalid item number.")
            elif new_cat not in cats:
                print(f"  Unknown category. Options: {', '.join(cats)}")
            else:
                inv["items"][idx]["category"] = new_cat
                print(f"  {GREEN}#{idx+1} → {new_cat}{RESET}")
        else:
            print(f"  {DIM}Format: <number> <category>   e.g.  2 Food{RESET}")

    print(f"\n  {BOLD}Saving {len(inv['items'])} items  |  {inv['date']}  |  ₹{inv['total']:.2f}{RESET}")
    print(f"  {'─'*64}")
    for item in inv["items"]:
        print(f"  {item['name']:<36} {item['amount']:>8.2f}  {item['category']}")

    confirm = _ask(f"\n  Save all {len(inv['items'])} items? (y/n): ")
    if confirm.lower() != "y":
        print("  Cancelled.")
        return

    for item in inv["items"]:
        tracker.add_expense(
            amount=item["unit_price"],
            category=item["category"],
            description=item["name"],
            date=inv["date"],
            tags=["zepto", "invoice"],
            quantity=item["qty"],
        )
    print(f"\n  {GREEN}Done — {len(inv['items'])} items added  →  ₹{inv['total']:.2f}{RESET}")


def cmd_balance(tracker: ExpenseTracker):
    BOLD  = "\033[1m"
    GREEN = "\033[32m"
    RED   = "\033[31m"
    DIM   = "\033[2m"
    CYAN  = "\033[36m"
    RESET = "\033[0m"

    all_time = tracker.all_time_summary()
    total_income  = all_time["total_income"]
    total_expense = all_time["total_expense"]
    opening       = tracker.opening_balance
    expected_bal  = round(opening + total_income - total_expense, 2)
    by_method     = tracker.balance_by_payment_method()

    bal_color = GREEN if expected_bal >= 0 else RED
    sign      = "+" if expected_bal >= 0 else ""

    print(f"\n{CYAN}{BOLD}{'═'*54}{RESET}")
    print(f"{CYAN}{BOLD}  ACCOUNT BALANCE CHECK{RESET}")
    print(f"{CYAN}{BOLD}{'═'*54}{RESET}")

    if opening:
        print(f"  Opening balance    {BOLD}₹{opening:>12,.2f}{RESET}")
    print(f"  + Total income     {GREEN}{BOLD}₹{total_income:>12,.2f}{RESET}")
    print(f"  – Total expenses       ₹{total_expense:>12,.2f}")
    print(f"  {'─'*40}")
    print(f"  Expected balance   {bal_color}{BOLD}{sign}₹{expected_bal:>11,.2f}{RESET}")
    print(f"{CYAN}{BOLD}{'═'*54}{RESET}")

    # ── Per-payment-method breakdown ──────────────────────────────────────────
    if by_method:
        print(f"\n  {BOLD}Spending by payment method{RESET}  {DIM}(all-time expenses){RESET}")
        print(f"  {'─'*44}")
        for method, spent in by_method.items():
            pct = (spent / total_expense * 100) if total_expense else 0
            print(f"  {method:<16}  ₹{spent:>10,.2f}  {DIM}{pct:.0f}%{RESET}")
        print(f"  {'─'*44}")
        print(f"  {'Total':<16}  ₹{total_expense:>10,.2f}")

    print()

    # ── Option to set opening balance ─────────────────────────────────────────
    raw = _ask("  Set opening balance (Enter to skip): ").strip()
    if raw:
        try:
            tracker.set_opening_balance(float(raw))
            new_bal = round(float(raw) + total_income - total_expense, 2)
            color   = GREEN if new_bal >= 0 else RED
            sign    = "+" if new_bal >= 0 else ""
            print(f"  {color}{BOLD}Updated → expected balance: {sign}₹{new_bal:,.2f}{RESET}")
        except ValueError:
            print("  Invalid amount.")


def cmd_sync_gmail(tracker: ExpenseTracker):
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BOLD  = "\033[1m"
    RESET = "\033[0m"

    print("\n  Sync emails from the last how many days?")
    raw = _ask("  Days [30]: ") or "30"
    try:
        days = int(raw)
    except ValueError:
        print("  Invalid number.")
        return

    print(f"\n  {YELLOW}Dry run first — no entries will be saved yet.{RESET}")
    dry = _ask("  Run dry run first? (y/n) [y]: ").lower() or "y"

    if dry != "n":
        print("\n  Scanning emails (dry run)…")
        try:
            results = tracker.sync_from_gmail(since_days=days, dry_run=True)
            if not results:
                print("  No new transactions found.")
                return
            print(f"\n  Found {len(results)} new transaction(s):")
            for r in results:
                print(f"    {r['date']}  {r['category']:<16} ₹{r['amount']:>8.2f}  {r['description']}")
            confirm = _ask(f"\n  Import all {len(results)}? (y/n): ").lower()
            if confirm != "y":
                print("  Cancelled.")
                return
        except Exception as e:
            print(f"  Error: {e}")
            return

    print("\n  Importing…")
    try:
        results = tracker.sync_from_gmail(since_days=days, dry_run=False)
        print(f"\n  {GREEN}{BOLD}Imported {len(results)} transaction(s).{RESET}")
    except Exception as e:
        print(f"  Error: {e}")


def main():
    tracker = ExpenseTracker()
    MENU = {
        "1": ("Add expense",          cmd_add),
        "2": ("Import invoice (PDF)", cmd_import_invoice),
        "3": ("List expenses",        cmd_list),
        "4": ("Edit expense",         cmd_edit),
        "5": ("Delete expense",       cmd_delete),
        "6": ("Monthly summary",      cmd_summary),
        "7": ("Set budgets",          cmd_budget),
        "8": ("Account balance",      cmd_balance),
        "9": ("Sync from Gmail",      cmd_sync_gmail),
        "10": ("Quit",                None),
    }
    print("\nExpense Tracker")
    while True:
        print()
        for key, (label, _) in MENU.items():
            print(f"  {key}. {label}")
        choice = input("Choice: ").strip().lstrip("0")
        if choice not in MENU:
            continue
        label, fn = MENU[choice]
        if fn is None:  # Quit
            print("Bye.")
            break
        try:
            fn(tracker)
        except KeyboardInterrupt:
            print()


if __name__ == "__main__":
    main()
