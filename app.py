"""Flask web UI for Expense Tracker."""
from flask import Flask, jsonify, request, send_file
from pathlib import Path
import os, sys

sys.path.insert(0, str(Path(__file__).parent))
from main import ExpenseTracker, PAYMENT_METHODS, CATEGORY_SUGGESTIONS

app = Flask(__name__)
_tracker = ExpenseTracker()


@app.before_request
def _reload():
    """Re-read from disk/DB before every request so changes from CLI or other workers are visible."""
    _tracker._load()


def _e(expense):
    return {
        "id":             expense.id,
        "amount":         expense.amount,
        "total":          expense.total,
        "quantity":       expense.quantity,
        "category":       expense.category,
        "description":    expense.description,
        "date":           expense.date,
        "entry_type":     expense.entry_type,
        "payment_method": expense.payment_method,
        "tags":           expense.tags,
    }


@app.route("/")
def index():
    return send_file(Path(__file__).parent / "templates" / "index.html")


# ── Expenses ──────────────────────────────────────────────────────────────────

@app.route("/api/expenses", methods=["GET"])
def list_expenses():
    month    = request.args.get("month") or None
    category = request.args.get("category") or None
    etype    = request.args.get("type") or None
    exps     = _tracker.get_expenses(category=category, month=month)
    if etype:
        exps = [e for e in exps if e.entry_type == etype]
    return jsonify([_e(e) for e in exps])


@app.route("/api/expenses", methods=["POST"])
def create_expense():
    d = request.get_json(force=True)
    try:
        exp = _tracker.add_expense(
            amount         = float(d["amount"]),
            category       = d["category"],
            description    = d.get("description", ""),
            date           = d.get("date") or None,
            tags           = d.get("tags", []),
            entry_type     = d.get("entry_type", "expense"),
            quantity       = float(d.get("quantity", 1.0)),
            payment_method = d.get("payment_method", ""),
        )
        return jsonify(_e(exp)), 201
    except (ValueError, KeyError) as err:
        return jsonify({"error": str(err)}), 400


@app.route("/api/expenses/<eid>", methods=["DELETE"])
def delete_expense(eid):
    try:
        _tracker.delete_expense(eid)
        return "", 204
    except ValueError as err:
        return jsonify({"error": str(err)}), 404


# ── Categories ────────────────────────────────────────────────────────────────

@app.route("/api/categories", methods=["GET"])
def list_categories():
    etype = request.args.get("type") or None
    cats  = _tracker.list_categories(entry_type=etype)
    return jsonify([{
        "name": c.name,
        "monthly_budget": c.monthly_budget,
        "entry_type": c.entry_type,
    } for c in cats])


@app.route("/api/categories/<name>/budget", methods=["PUT"])
def set_budget(name):
    d = request.get_json(force=True)
    try:
        _tracker.set_budget(name, float(d["budget"]))
        return jsonify({"ok": True})
    except ValueError as err:
        return jsonify({"error": str(err)}), 400


# ── Summaries ─────────────────────────────────────────────────────────────────

@app.route("/api/summary", methods=["GET"])
def get_summary():
    from datetime import date
    month   = request.args.get("month", date.today().strftime("%Y-%m"))
    summary = _tracker.monthly_summary(month)
    status  = _tracker.budget_status(month)
    return jsonify({**summary, "budget_status": status})


@app.route("/api/balance", methods=["GET"])
def get_balance():
    at = _tracker.all_time_summary()
    bm = _tracker.balance_by_payment_method()
    return jsonify({
        **at,
        "opening_balance":  _tracker.opening_balance,
        "expected_balance": round(_tracker.opening_balance + at["total_income"] - at["total_expense"], 2),
        "by_payment_method": [{"method": k, "amount": v} for k, v in bm.items()],
    })


@app.route("/api/balance/opening", methods=["PUT"])
def set_opening():
    d = request.get_json(force=True)
    try:
        _tracker.set_opening_balance(float(d["amount"]))
        return jsonify({"ok": True})
    except (ValueError, KeyError) as err:
        return jsonify({"error": str(err)}), 400


@app.route("/api/months", methods=["GET"])
def get_months():
    months = sorted({e.date[:7] for e in _tracker.expenses}, reverse=True)
    return jsonify(months)


@app.route("/api/meta", methods=["GET"])
def get_meta():
    return jsonify({
        "payment_methods":      PAYMENT_METHODS,
        "category_suggestions": CATEGORY_SUGGESTIONS,
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"\n  Expense Tracker — Web UI")
    print(f"  Open  http://localhost:{port}  in your browser\n")
    app.run(debug=False, host='0.0.0.0', port=port)
