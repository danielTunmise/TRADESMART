import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tradesmart_project.settings')
django.setup()

from analytics.models import Trade, TradingAccount
from django.contrib.auth.models import User

def fix():
    # 1. Grab the very first User and Account found in the entire database
    user = User.objects.all().first()
    account = TradingAccount.objects.all().first()

    if not user:
        print("CRITICAL: No User found in database. Please register on the website first!")
        return
    if not account:
        print(f"CRITICAL: User '{user.username}' exists, but has no Trading Account. Create one in the Dashboard!")
        return

    print(f"--- Repairing Data ---")
    print(f"Targeting User: {user.username}")
    print(f"Targeting Account: {account.nickname}")

    # 2. Force every single trade in the database to belong to this user and account
    trades = Trade.objects.all()
    updated_count = trades.update(user=user, account=account)

    print(f"Success! {updated_count} trades are now linked and should be visible.")

if __name__ == "__main__":
    fix()