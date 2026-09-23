"""Standalone bill-reminder check - runs the same digest logic as the app's
background scheduler, but doesn't need Flask or a browser open. Safe to run
from Windows Task Scheduler (see schedule_reminders.bat).
"""
import os
import sys

import notifications

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "budget.db")

if __name__ == "__main__":
    if not os.path.exists(DB_PATH):
        print(f"No database found at {DB_PATH} - run the app at least once first.")
        sys.exit(1)
    force = "--force" in sys.argv
    result = notifications.run_daily_check(DB_PATH, force=force)
    print(result)
