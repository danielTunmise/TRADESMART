from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone
from analytics.models import Trade, TradingAccount
from datetime import timedelta

class Command(BaseCommand):
    help = 'Sends monthly performance reports to users on the 1st of the month'

    def handle(self, *args, **kwargs):
        today = timezone.now().date()
        
        # 1. CHECK: Only run this on the 1st of the month
        # (Remove this check if you want to force-test the script manually)
        if today.day != 1:
             self.stdout.write(self.style.WARNING("Today is not the 1st. Skipping report generation."))
             return

        # 2. Calculate "Last Month"
        # We go back 1 day from the 1st to get the previous month
        last_month_date = today - timedelta(days=1)
        target_year = last_month_date.year
        target_month = last_month_date.month
        month_name = last_month_date.strftime("%B")

        self.stdout.write(f"Generating reports for: {month_name} {target_year}")

        # 3. Loop through all users
        users = User.objects.all()
        
        for user in users:
            # Get user's accounts
            accounts = TradingAccount.objects.filter(user=user)
            if not accounts.exists():
                continue

            # Fetch trades for last month
            trades = Trade.objects.filter(
                account__in=accounts,
                close_time__year=target_year,
                close_time__month=target_month
            )

            if not trades.exists():
                continue # No trades, no report needed

            # 4. Calculate Summary Stats
            total_pnl = sum(float(t.profit) for t in trades)
            win_trades = [t for t in trades if float(t.profit) > 0]
            count = len(trades)
            win_rate = int((len(win_trades) / count) * 100) if count > 0 else 0
            
            # Formatting
            pnl_str = f"${total_pnl:,.2f}"
            if total_pnl > 0: pnl_str = "+" + pnl_str
            emoji = "🚀" if total_pnl >= 0 else "📉"

            # 5. Construct Email
            subject = f"Your {month_name} Trading Report is Ready! {emoji}"
            
            message = f"""
            Hi {user.first_name or user.username},

            Your performance report for {month_name} {target_year} has been finalized.

            --- QUICK SUMMARY ---
            💰 Net P&L:  {pnl_str}
            🎯 Win Rate: {win_rate}%
            📊 Trades:   {count}
            ---------------------

            Your detailed "Wrapped" story with pattern analysis, best sessions, and hidden insights is waiting for you.

            👉 visit the TradeSmart app to view your performance 
            

            Keep pushing!
            The TradeSmart Team
            """

            # 6. Send Email
            try:
                send_mail(
                    subject,
                    message,
                    settings.EMAIL_HOST_USER,
                    [user.email],
                    fail_silently=False,
                )
                self.stdout.write(self.style.SUCCESS(f"Sent report to {user.email}"))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Failed to send to {user.email}: {e}"))