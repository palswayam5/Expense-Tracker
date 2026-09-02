"""
Run this ONCE after setting up Supabase to push your local expenses.json to the cloud.

Usage:
    DATABASE_URL="postgresql://..." python3 migrate_to_cloud.py
"""
import os, sys, json
from pathlib import Path

db_url = os.environ.get("DATABASE_URL")
if not db_url:
    print("Set DATABASE_URL first:\n  DATABASE_URL='postgresql://...' python3 migrate_to_cloud.py")
    sys.exit(1)

if not Path("expenses.json").exists():
    print("No local expenses.json found — nothing to migrate.")
    sys.exit(0)

from main import ExpenseTracker, DEFAULT_CATEGORIES

# Load from local file (bypass DATABASE_URL by temporarily unsetting it)
del os.environ["DATABASE_URL"]
tracker = ExpenseTracker()
print(f"Loaded {len(tracker.expenses)} entries from expenses.json")

# Save to cloud DB
os.environ["DATABASE_URL"] = db_url
try:
    import psycopg2
except ImportError:
    print("psycopg2 not installed. Run:  pip3 install psycopg2-binary")
    sys.exit(1)

tracker._save_to_db(db_url)
print(f"Done! {len(tracker.expenses)} entries pushed to cloud database.")
print("You can now deploy and your data will be there.")
