import hashlib
import pytz
from datetime import datetime
from django.utils import timezone
from .models import Trade, TradingAccount
from .cloud_connector import TradeSmartCloud

# --- 1. HELPER FUNCTIONS ---

def extract_clean_symbol(symbol_obj):
    """
    Parses SnapTrade symbol data which can be a dict or a string.
    Returns a clean string like 'EURUSD' or 'AAPL'.
    """
    if isinstance(symbol_obj, dict):
        # SnapTrade often returns: {'id': '...', 'symbol': 'AAPL', ...}
        return symbol_obj.get('symbol', 'UNKNOWN')
    elif isinstance(symbol_obj, str):
        return symbol_obj
    return "UNKNOWN"

def generate_trade_id(trade_data):
    """
    Creates a unique ID for a trade to prevent duplicates.
    Uses the Broker's Order ID if available, otherwise hashes the trade details.
    """
    # 1. Prefer the official ID from the broker/API
    if 'id' in trade_data and trade_data['id']:
        return str(trade_data['id'])
    
    # 2. Fallback: Create a hash based on specific trade details
    # We combine Symbol + Time + Price + Units to make a unique fingerprint
    # Safe getters to avoid crashes if keys are missing
    symbol = extract_clean_symbol(trade_data.get('symbol') or trade_data.get('universal_symbol'))
    time_exec = trade_data.get('time_executed') or trade_data.get('open_time')
    price = trade_data.get('execution_price') or trade_data.get('price') or trade_data.get('open_price')
    units = trade_data.get('filled_quantity') or trade_data.get('volume')
    
    raw_str = f"{symbol}-{time_exec}-{price}-{units}"
    return hashlib.md5(raw_str.encode()).hexdigest()

def get_active_account(request):
    """
    Retrieves the currently active TradingAccount based on session.
    If none selected, defaults to the first available account.
    Returns: (current_account, all_user_accounts)
    """
    user_accounts = TradingAccount.objects.filter(user=request.user)
    
    # 1. Try to get ID from session
    active_id = request.session.get('active_account_id')
    
    current_account = None
    
    if active_id:
        # Check if this ID actually belongs to the user
        current_account = user_accounts.filter(id=active_id).first()
    
    # 2. Fallback: If session ID is invalid or missing, pick the first account
    if not current_account and user_accounts.exists():
        current_account = user_accounts.first()
        # Update session to match
        request.session['active_account_id'] = current_account.id
        
    return current_account, user_accounts


# --- 2. CORE SYNC ENGINE ---

def sync_account_trades(account_id):
    """
    The Master Sync Function (SnapTrade Only).
    1. Connects to SnapTrade Cloud API.
    2. Downloads latest history for the specific account.
    3. Smart-Inserts into Database (skips duplicates).
    """
    # 1. Get Account & SDK
    try:
        account = TradingAccount.objects.get(id=account_id)
        client = TradeSmartCloud()
    except Exception as e:
        print(f"❌ Sync Error: Account {account_id} not found. {e}")
        return False

    # Check if this is a SnapTrade account
    if not account.snaptrade_user_id or not account.user_secret:
        print(f"ℹ️ Account '{account.broker_name}' is Manual. Skipping cloud sync.")
        return False

    print(f"🔄 Starting SnapTrade Sync for: {account.broker_name}...")
    raw_trades = []

    # ---------------------------------------------------------
    # SNAPTRADE CONNECTION (OAUTH)
    # ---------------------------------------------------------
    try:
        # Fetching all accounts to find the right ID (usually the first one for the user)
        # In a real app, you might want to store the specific 'brokerage_account_id' in your model
        st_accounts = client.get_accounts(account.snaptrade_user_id, account.user_secret)
        target_acc_id = None
        
        if st_accounts:
            # For now, we assume the user has one brokerage connection per SnapTrade user
            target_acc_id = st_accounts[0]['id'] 
        
        if not target_acc_id:
            print("⚠️ No linked brokerage account found in SnapTrade.")
            return False

        # Fetch History
        raw_list = client.fetch_history(
            account.snaptrade_user_id, 
            account.user_secret, 
            target_acc_id
        )
        
        # Add to main list
        if raw_list:
            raw_trades.extend(raw_list)
        else:
            print("ℹ️ SnapTrade returned no new trades.")

    except Exception as e:
        print(f"❌ SnapTrade Sync Error: {e}")
        return False
    
    # ---------------------------------------------------------
    # 3. SAVE TO DATABASE (Universal Logic)
    # ---------------------------------------------------------
    new_count = 0
    for order in raw_trades:
        try:
            # Generate Unique ID
            trade_uid = generate_trade_id(order)
            
            # Check if exists
            if Trade.objects.filter(trade_id=trade_uid).exists():
                continue

            # Parse Data
            sym_obj = order.get('universal_symbol') or order.get('symbol')
            symbol = extract_clean_symbol(sym_obj)
            
            # Get Action (Standardized to BUY/SELL)
            action = order.get('action', '').upper()
            if action not in ['BUY', 'SELL']:
                continue # Skip non-trade records (e.g. DIVIDEND)

            direction = 'LONG' if action == 'BUY' else 'SHORT'
            price = float(order.get('execution_price') or order.get('price') or 0.0)
            units = float(order.get('filled_quantity') or 0.0)
            
            # Handle Timezone (SnapTrade returns ISO string)
            raw_time = order.get('time_executed')
            if isinstance(raw_time, str):
                try:
                    open_time = datetime.fromisoformat(raw_time.replace('Z', '+00:00'))
                except:
                    open_time = timezone.now()
            else:
                open_time = timezone.now()

            # Create Trade Record
            Trade.objects.create(
                user=account.user,
                account=account,
                trade_id=trade_uid,
                symbol=symbol,
                direction=direction,
                lot_size=units,
                open_price=price,
                close_price=price, # Will update when we handle exits logic later
                profit=0.0, 
                open_time=open_time,
                source_platform='SnapTrade'
            )
            new_count += 1
            
        except Exception as e:
            print(f"⚠️ Failed to save trade {trade_uid}: {e}")
            continue

    print(f"✅ Sync Complete. Imported {new_count} new trades.")
    return True