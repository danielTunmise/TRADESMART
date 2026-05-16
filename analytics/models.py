from django.db import models
from django.contrib.auth.models import User

# --- 1. TRADING ACCOUNT (MERGED) ---
# This single class now handles BOTH the API credentials AND the account locking.
class TradingAccount(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    
    # [From First Definition] API Credentials for SnapTrade
    snaptrade_user_id = models.CharField(max_length=100, unique=True, null=True, blank=True)
    user_secret = models.CharField(max_length=200, null=True, blank=True)
    
    broker_name = models.CharField(max_length=100, default="Unknown Broker")
    
    # [From Second Definition] Security Lock
    # This stores the account number (e.g., "105465177") to prevent mixing data
    account_number = models.CharField(max_length=50, null=True, blank=True) 

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} - {self.broker_name}"

# --- NEW TABLE: ACCOUNT BALANCE ---
class AccountBalance(models.Model):
    # We use 'analytics.TradingAccount' to ensure Django finds the model correctly
    account = models.OneToOneField('analytics.TradingAccount', on_delete=models.CASCADE, related_name='balance')
    amount = models.DecimalField(max_digits=15, decimal_places=2, default=0.00)
    last_updated = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.account.broker_name}: ${self.amount}"

# --- 2. USER PROFILE ---
class UserProfile(models.Model):
    RISK_CHOICES = [
        ('conservative', 'Conservative (0.5-1%)'),
        ('moderate', 'Moderate (1-2%)'),
        ('aggressive', 'Aggressive (>2%)'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE)
    risk_appetite = models.CharField(max_length=20, choices=RISK_CHOICES, default='moderate')
    daily_loss_limit = models.DecimalField(max_digits=10, decimal_places=2, default=500.00)
    avatar = models.ImageField(upload_to='avatars/', blank=True, null=True)

    def __str__(self):
        return f"{self.user.username} - {self.risk_appetite}"

# --- 3. TRADE ---
class Trade(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    account = models.ForeignKey('analytics.TradingAccount', on_delete=models.CASCADE, related_name='trades', null=True)
    
    trade_id = models.CharField(max_length=100, unique=True, null=True, blank=True) 
    symbol = models.CharField(max_length=20, null=True, blank=True)
    direction = models.CharField(max_length=10, null=True, blank=True)
    
    lot_size = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    profit = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    
    open_price = models.DecimalField(max_digits=12, decimal_places=5, default=0.00000)
    close_price = models.DecimalField(max_digits=12, decimal_places=5, default=0.00000)
    
    # --- NEW FIELDS (SL/TP/Swap/Comm) ---
    stop_loss = models.DecimalField(max_digits=12, decimal_places=5, null=True, blank=True)
    take_profit = models.DecimalField(max_digits=12, decimal_places=5, null=True, blank=True)
    swap = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    commission = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    # ------------------------------------

    strategy_tag = models.CharField(max_length=50, blank=True, null=True)
    source_platform = models.CharField(max_length=50, default="Cloud-Sync") 
    status = models.CharField(max_length=20, default='CLOSED') 

    open_time = models.DateTimeField(null=True, blank=True)
    close_time = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.symbol} | {self.profit}"

# --- 4. TRADE NOTE ---
class TradeNote(models.Model):
    trade = models.ForeignKey(Trade, on_delete=models.CASCADE, related_name='notes')
    content = models.TextField()
    screenshot = models.ImageField(upload_to='trade_screenshots/', blank=True, null=True)
    sentiment_score = models.FloatField(default=0.0) 
    emotion_tag = models.CharField(max_length=50, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True) 

    def __str__(self):
        return f"Note for {self.trade.trade_id}"

# --- 5. STRATEGY & CHECKLIST (UPDATED) ---
class TradingStrategy(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='strategies')
    name = models.CharField(max_length=100) # e.g., "Scalping", "Swing"
    description = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.user.username})"

class ChecklistRule(models.Model):
    # NOW LINKED TO STRATEGY, NOT DIRECTLY TO USER
    strategy = models.ForeignKey(TradingStrategy, on_delete=models.CASCADE, related_name='rules')
    text = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.text 