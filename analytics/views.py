from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import User
from django.http import HttpResponse, JsonResponse
from django.utils.timezone import make_aware
from datetime import datetime, timedelta
from reportlab.pdfgen import canvas
import asyncio
from django.conf import settings
from asgiref.sync import async_to_sync
import calendar
import ast 
import hashlib
from .models import Trade, UserProfile, TradeNote, TradingAccount, AccountBalance
from .forms import ProfileUpdateForm, TradeImportForm
from django.db.models import Sum
from django.utils import timezone
import uuid
import pandas as pd
from django.contrib import messages
from .cloud_connector import TradeSmartCloud
import io
import math
import re # Added for robust number extraction
from django.urls import reverse
from django.core import signing # Import for encryption
from .utils import sync_account_trades
import yfinance as yf
import pandas as pd
import numpy as np
import re
from django.contrib.auth import authenticate, login
from django.shortcuts import render, redirect
import random
import time
from django.core.mail import send_mail
from django.http import JsonResponse
from django.conf import settings
from django.db.models import Sum
from datetime import datetime
from django.db.models import Sum, Count, F, Avg, Case, When, Value, CharField
from django.db.models.functions import ExtractHour, ExtractWeekDay
from django.http import JsonResponse
from datetime import datetime, timedelta
import statistics
from django.views.decorators.http import require_POST
import json
from .models import ChecklistRule
from .models import ChecklistRule, TradingStrategy
from django.contrib.auth import logout
from datetime import timedelta
from django.utils.dateparse import parse_datetime
from django.db.models import Q, Prefetch
import socket
from django.core.paginator import Paginator
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login
from django.contrib.auth.models import User
from django.contrib.auth.views import PasswordResetView
from .forms import CustomPasswordResetForm
import traceback 


class CustomResetView(PasswordResetView):
    # Use our custom form that checks the DB
    form_class = CustomPasswordResetForm
    template_name = 'password_change.html'
    extra_context = {'mode': 'request'}
    
    # Where to go after sending the email (matches the name in urls.py)
    success_url = '/password-reset/done/'

def extract_clean_symbol(obj):
    """
    Recursively digs through nested dictionaries to find a clean symbol string.
    """
    if not obj: return "Unknown"
    
    if isinstance(obj, str):
        try:
            if obj.strip().startswith('{'):
                obj = ast.literal_eval(obj)
            else:
                return obj 
        except:
            return obj

    if isinstance(obj, dict):
        if 'raw_symbol' in obj and obj['raw_symbol']:
            return extract_clean_symbol(obj['raw_symbol'])
        
        if 'symbol' in obj and obj['symbol']:
            val = obj['symbol']
            if isinstance(val, str) and not val.strip().startswith('{'):
                return val
            return extract_clean_symbol(val)
            
        if 'description' in obj: return obj['description']
        if 'name' in obj: return obj['name']

    return str(obj)

def generate_trade_id(order):
    """
    Generates a deterministic unique ID based on trade properties.
    Crucial for when API returns None for IDs.
    """
    if order.get('id'):
        return str(order.get('id'))
    
    # Create unique signature: Time + Symbol + Action + Units + Price
    raw_sig = f"{order.get('time_executed')}-{order.get('symbol')}-{order.get('action')}-{order.get('filled_quantity')}-{order.get('execution_price')}"
    return hashlib.md5(raw_sig.encode()).hexdigest()

def generate_linking_key(symbol, quantity):
    """
    Creates a unique key based on Symbol + Quantity.
    This acts as the bridge between Live positions and Closed trades.
    """
    try:
        # Standardize quantity to 4 decimal places to ensure match (e.g. 1 vs 1.0000)
        qty_clean = f"{float(quantity):.4f}"
    except:
        qty_clean = str(quantity)
    return f"{symbol}_{qty_clean}"

def get_active_account(request):
    user_accounts = TradingAccount.objects.filter(user=request.user)
    if not user_accounts.exists():
        return None, user_accounts

    selected_id = request.GET.get('account_id')
    if selected_id:
        request.session['active_account_id'] = selected_id
        current_account = user_accounts.filter(id=selected_id).first()
    else:
        session_id = request.session.get('active_account_id')
        if session_id:
            current_account = user_accounts.filter(id=session_id).first()
        else:
            current_account = user_accounts.first()
            if current_account:
                request.session['active_account_id'] = current_account.id
    
    if not current_account:
        current_account = user_accounts.first()

    return current_account, user_accounts


# --- 1. AUTHENTICATION ---
def register_view(request):
    if request.method == 'POST':
        # 1. Get the email from the HTML form (because name="email")
        email = request.POST.get('email') 
        
        username = request.POST.get('username')
        password = request.POST.get('password')
        password_confirm = request.POST.get('password_confirm')
        first_name = request.POST.get('first_name')
        last_name = request.POST.get('last_name')
        risk = request.POST.get('risk_appetite', 'moderate').lower()

        if password != password_confirm:
            return render(request, 'register.html', {'error': 'Passwords do not match'})

        # Optional: Check if email is already taken
        if User.objects.filter(email=email).exists():
            return render(request, 'register.html', {'error': 'Email already registered'})

        if not User.objects.filter(username=username).exists():
            # 2. Pass the 'email' variable to create_user to save it to the DB
            user = User.objects.create_user(
                username=username, 
                email=email,  # <--- THIS IS THE CRITICAL LINE
                password=password, 
                first_name=first_name, 
                last_name=last_name
            )
            
            # Create the profile settings
            UserProfile.objects.get_or_create(user=user, defaults={'risk_appetite': risk})
            
            return redirect('login')
        else:
            return render(request, 'register.html', {'error': 'Username already taken'})
            
    return render(request, 'register.html')

def login_view(request):
    if request.method == 'POST':
        # 1. Get the raw input (user could have typed an email or a username)
        username_input = request.POST.get('username')  
        password_input = request.POST.get('password')
        remember_me = request.POST.get('remember')

        # 2. Check if the input exists as an EMAIL in the database
        try:
            # Use 'iexact' for case-insensitive email matching
            user_obj = User.objects.get(email__iexact=username_input)
            # If found, retrieve the actual username associated with that email
            actual_username = user_obj.username
        except User.DoesNotExist:
            # If no user found with that email, assume the input IS the username
            actual_username = username_input
        except User.MultipleObjectsReturned:
            # Safety net: if multiple users somehow share an email, take the first one
            user_obj = User.objects.filter(email__iexact=username_input).first()
            actual_username = user_obj.username

        # 3. Authenticate using the resolved real username
        user = authenticate(request, username=actual_username, password=password_input)

        if user is not None:
            login(request, user)
            
            # Handle "Remember Me"
            if remember_me:
                # Keep the session for 2 weeks (1209600 seconds)
                request.session.set_expiry(1209600)
            else:
                # Expire session on browser close
                request.session.set_expiry(0) 
            
            return redirect('dashboard')
        else:
            return render(request, 'login.html', {'error': 'Invalid credentials'})

    return render(request, 'login.html')
# --- 2. BROKER SYNC ---
@login_required
def sync_trading_data(request):
    """
    Triggered when user clicks 'Sync Data' on Dashboard.
    """
    from .utils import sync_account_trades # Import the function we just wrote

    if request.method == 'POST':
        # Get account ID from the form (if multiple accounts)
        account_id = request.POST.get('account_id') or request.session.get('active_account_id')
        
        if account_id:
            # Run the sync
            success = sync_account_trades(account_id)
            
            if success:
                messages.success(request, "Sync complete! Dashboard updated.")
            else:
                messages.warning(request, "Sync finished with no new data or minor errors.")
        else:
            messages.error(request, "No account selected to sync.")
            
    return redirect('dashboard')

# --- 3. DASHBOARD ---
# In analytics/views.py

@login_required
def dashboard(request):
    # --- SAFE FLOAT HELPER ---
    # Prevents API data inconsistencies from crashing the trade loop
    def _safe_float(val):
        try:
            if val is None or str(val).strip() in ['', 'None', 'null']: 
                return 0.0
            return float(val)
        except (ValueError, TypeError):
            return 0.0

    # --- 0. HANDLE API CONNECTION RETURN (LAZY CREATION) ---
    if request.GET.get('connected') == 'true' and 'pending_api_data' in request.session:
        try:
            pending_data = request.session.pop('pending_api_data')
            new_account = TradingAccount.objects.create(
                user=request.user,
                broker_name=pending_data['broker_name'],
                snaptrade_user_id=pending_data['user_id'],
                user_secret=pending_data['user_secret']
            )
            request.session['active_account_id'] = new_account.id
            messages.success(request, f"Successfully connected {new_account.broker_name}!")
        except Exception as e:
            messages.error(request, f"Error finalizing connection: {str(e)}")

    # --- STANDARD DASHBOARD LOGIC STARTS HERE ---
    current_account, user_accounts = get_active_account(request)
    
    # --- 1. INITIALIZE VARIABLES ---
    stats = {'win_rate': 0, 'profit_factor': 0.0, 'day_win_rate': 0}
    global_stats = {'win_rate': 0, 'profit_factor': 0.0, 'day_win_rate': 0, 'net_pnl': 0.0}
    equity_curve = [{'date': 'Start', 'value': 0.0}]
    drawdown_curve = [{'date': 'Start', 'value': 0.0}]
    monthly_days = []
    daily_map = {}
    positions = []
    
    is_connected = False
    live_balance = 0.00
    buying_power = 0.00
    
    all_processed_trades = []

    # --- 2. FETCH TRADES (LOGIC SHARED FOR BOTH PAGE LOAD AND AJAX) ---
    if current_account:
        # PATH A: API ACCOUNT (Fetch from Cloud)
        if current_account.snaptrade_user_id:
            try:
                client = TradeSmartCloud()
                ST_UID = current_account.snaptrade_user_id
                ST_SECRET = current_account.user_secret
                
                cloud_accounts = client.get_accounts(ST_UID, ST_SECRET)
                if cloud_accounts:
                    is_connected = True
                    acc_id = cloud_accounts[0]['id']
                    
                    # Only fetch balance on full page load (optimization)
                    if not request.headers.get('x-requested-with') == 'XMLHttpRequest':
                        balance_data = client.api.account_information.get_user_account_balance(ST_UID, ST_SECRET, acc_id)
                        if balance_data.body:
                            live_balance = float(balance_data.body[0].get('cash', 0) or 0.0)
                            buying_power = balance_data.body[0].get('buying_power', 0)

                    # Fetch Orders (Deep Fetch: 365 Days)
                    orders_res = client.api.account_information.get_user_account_orders(ST_UID, ST_SECRET, acc_id, state='all', days=365)
                    all_orders = orders_res.body if isinstance(orders_res.body, list) else []
                    
                    chron_orders = sorted(all_orders, key=lambda x: x.get('time_executed') or x.get('time_placed') or '0000-00-00', reverse=False)
                    inventory = {} 

                    for order in chron_orders:
                        # Date Parsing
                        raw_date = (order.get('time_executed') or order.get('time_placed') or order.get('time_updated') or order.get('execution_time'))
                        trade_dt = timezone.now()
                        if raw_date:
                            try:
                                if str(raw_date).isdigit(): 
                                    trade_dt = datetime.fromtimestamp(int(raw_date) / 1000.0)
                                    trade_dt = make_aware(trade_dt)
                                else: 
                                    trade_dt = datetime.fromisoformat(str(raw_date).replace('Z', '+00:00'))
                            except: pass
                        
                        d_str = trade_dt.strftime("%Y-%m-%d")

                        # Data Extraction & FIFO Logic
                        sym_obj = order.get('universal_symbol') or order.get('symbol')
                        symbol = extract_clean_symbol(sym_obj)
                        action = order.get('action', '').upper()
                        units = float(order.get('filled_quantity') or order.get('total_quantity') or 0.0)
                        price = float(order.get('execution_price') or order.get('price') or 0.0)
                        
                        # SAFELY EXTRACT SL/TP
                        sl = _safe_float(order.get('stop_loss'))
                        tp = _safe_float(order.get('take_profit'))
                        
                        trade_pnl = 0.0; trade_closed = False; closing_price = price; opening_price = 0.0
                        
                        if symbol not in inventory: inventory[symbol] = {'qty': 0.0, 'avg_price': 0.0}
                        current_qty = inventory[symbol]['qty']; avg_entry = inventory[symbol]['avg_price']

                        if action == 'BUY':
                            if current_qty < 0: # Closing Short
                                opening_price = avg_entry; cover_qty = min(abs(current_qty), units)
                                trade_pnl = (avg_entry - price) * cover_qty; trade_closed = True
                                inventory[symbol]['qty'] += cover_qty
                                if inventory[symbol]['qty'] == 0: inventory[symbol]['avg_price'] = 0.0
                                if units - cover_qty > 0: inventory[symbol]['qty'] += (units - cover_qty); inventory[symbol]['avg_price'] = price 
                            else: # Opening Long
                                total_val = (current_qty * avg_entry) + (units * price); new_total_qty = current_qty + units
                                if new_total_qty > 0: inventory[symbol]['avg_price'] = total_val / new_total_qty
                                inventory[symbol]['qty'] = new_total_qty
                        elif action == 'SELL':
                            if current_qty > 0: # Closing Long
                                opening_price = avg_entry; close_qty = min(current_qty, units)
                                trade_pnl = (price - avg_entry) * close_qty; trade_closed = True
                                inventory[symbol]['qty'] -= close_qty
                                if inventory[symbol]['qty'] == 0: inventory[symbol]['avg_price'] = 0.0
                                if units - close_qty > 0: inventory[symbol]['qty'] -= (units - close_qty); inventory[symbol]['avg_price'] = price 
                            else: # Opening Short
                                total_val = (abs(current_qty) * avg_entry) + (units * price); new_total_abs_qty = abs(current_qty) + units
                                if new_total_abs_qty > 0: inventory[symbol]['avg_price'] = total_val / new_total_abs_qty
                                inventory[symbol]['qty'] -= units
                        
                        if trade_closed:
                            all_processed_trades.append({
                                'profit': round(trade_pnl, 2),
                                'sort_date': trade_dt,
                                'date_str': d_str,
                                'trade_id': generate_trade_id(order),
                                'symbol': symbol,
                                'direction': 'LONG' if action == 'SELL' else 'SHORT',
                                'lot_size': units,
                                'open_price': round(opening_price, 2),
                                'close_price': round(closing_price, 2),
                                'stop_loss': sl,
                                'take_profit': tp,
                                'source': 'API'
                            })
            except Exception as e:
                print(f"Dashboard API Error: {e}")

        # PATH B: MANUAL ACCOUNT (Fetch from Database)
        else:
            is_connected = True
            try:
                # Fetch Balance
                balance_obj = AccountBalance.objects.filter(account=current_account).first()
                if balance_obj:
                    live_balance = float(balance_obj.amount)
                    buying_power = live_balance
                
                # Fetch Trades
                db_trades_qs = Trade.objects.filter(account=current_account).values(
                    'profit', 'close_time', 'open_time', 'trade_id', 'symbol', 'direction',
                    'lot_size', 'open_price', 'close_price', 'stop_loss', 'take_profit'
                )

                for t in db_trades_qs:
                    sort_dt = t['close_time'] if t['close_time'] else (t['open_time'] if t['open_time'] else timezone.now())
                    d_str = sort_dt.strftime("%Y-%m-%d")
                    all_processed_trades.append({
                        'profit': float(t['profit']),
                        'sort_date': sort_dt,
                        'date_str': d_str,
                        'trade_id': t['trade_id'],
                        'symbol': t['symbol'],
                        'direction': t['direction'],
                        'lot_size': float(t['lot_size']),
                        'open_price': float(t['open_price']),
                        'close_price': float(t['close_price']),
                        'stop_loss': _safe_float(t.get('stop_loss')),
                        'take_profit': _safe_float(t.get('take_profit')),
                        'source': 'Database'
                    })
            except Exception as e:
                print(f"Dashboard DB Error: {e}")

    # --- 3. PROCESS TRADES INTO DAILY MAP & GLOBAL STATS ---
    all_processed_trades.sort(key=lambda x: x['sort_date'])
    
    total_wins = 0; total_losses = 0; gross_profit = 0.0; gross_loss = 0.0
    today_wins = 0; today_total = 0; running_equity = 0.0; peak_equity = 0.0
    today_str = str(timezone.now().date())

    for t in all_processed_trades:
        pnl = t['profit']
        
        # Stats
        if pnl > 0: 
            total_wins += 1; gross_profit += pnl
            if t['date_str'] == today_str: today_wins += 1
        else: 
            total_losses += 1; gross_loss += abs(pnl)

        if t['date_str'] == today_str: today_total += 1
        
        # Equity
        running_equity += pnl
        if running_equity > peak_equity: peak_equity = running_equity
        dd = running_equity - peak_equity
        
        equity_curve.append({'date': t['date_str'], 'value': round(running_equity, 2)})
        drawdown_curve.append({'date': t['date_str'], 'value': round(dd, 2)})
        
        # Daily Map (Crucial for Calendar)
        d_str = t['date_str']
        if d_str not in daily_map: daily_map[d_str] = {'pnl': 0.0, 'count': 0}
        daily_map[d_str]['pnl'] += pnl
        daily_map[d_str]['count'] += 1

    # Final All-Time Stats
    total_count = total_wins + total_losses
    if total_count > 0: global_stats['win_rate'] = int((total_wins / total_count) * 100)
    if gross_loss > 0: global_stats['profit_factor'] = round(gross_profit / gross_loss, 2)
    elif gross_profit > 0: global_stats['profit_factor'] = 99.9 
    if today_total > 0: global_stats['day_win_rate'] = int((today_wins / today_total) * 100)
    global_stats['net_pnl'] = round(running_equity, 2)
    
    # Recent Positions Table
    all_processed_trades.sort(key=lambda x: x['sort_date'], reverse=True)
    positions = all_processed_trades[:5]

    # --- AI BRIEFING CALCULATION ---
    ai_briefing = {'strengths': [], 'weaknesses': [], 'advice': []}
    
    if total_count > 0:
        avg_win = (gross_profit / total_wins) if total_wins > 0 else 0
        avg_loss = (gross_loss / total_losses) if total_losses > 0 else 0
        
        no_sl_count = 0
        no_tp_count = 0
        cut_losses_early = 0
        missed_tp = 0
        hit_tp = 0
        largest_loss = 0.0
        largest_win = 0.0
        
        for t in all_processed_trades:
            sl = t.get('stop_loss', 0.0) or 0.0
            tp = t.get('take_profit', 0.0) or 0.0
            entry = t['open_price']
            exit_p = t['close_price']
            pnl = t['profit']
            direction = t['direction']
            
            largest_loss = min(largest_loss, pnl)
            largest_win = max(largest_win, pnl)

            if sl == 0: no_sl_count += 1
            if tp == 0: no_tp_count += 1
            
            if pnl < 0 and sl > 0:
                if direction == 'LONG' and exit_p > sl: cut_losses_early += 1
                elif direction == 'SHORT' and exit_p < sl: cut_losses_early += 1
            
            # Check for TP adherence
            if pnl > 0 and tp > 0:
                if direction == 'LONG':
                    if exit_p < tp * 0.995: missed_tp += 1
                    elif exit_p >= tp * 0.995: hit_tp += 1
                elif direction == 'SHORT':
                    if exit_p > tp * 1.005: missed_tp += 1
                    elif exit_p <= tp * 1.005: hit_tp += 1

        # STRENGTHS (Always generate 3)
        # 1. Win Rate / PF
        if global_stats['win_rate'] >= 50:
            ai_briefing['strengths'].append(f"High precision execution: Solid win rate of {global_stats['win_rate']}%.")
        else:
            if global_stats['profit_factor'] >= 1.0:
                ai_briefing['strengths'].append(f"Resilient strategy: Despite a {global_stats['win_rate']}% win rate, your Profit Factor ({global_stats['profit_factor']}) keeps you profitable.")
            else:
                ai_briefing['strengths'].append("Active data logging: You are tracking trades consistently, the critical first step to discovering an edge.")

        # 2. Risk Asymmetry
        if avg_win > avg_loss and avg_loss > 0:
            ai_briefing['strengths'].append(f"Strong asymmetric returns: Your average win (${avg_win:.2f}) outweighs your average loss (${avg_loss:.2f}).")
        elif largest_win > abs(largest_loss) and largest_win > 0:
            ai_briefing['strengths'].append(f"Home run capability: Your best trade (${largest_win:.2f}) eclipsed your worst drawdown (${abs(largest_loss):.2f}).")
        else:
            ai_briefing['strengths'].append("Risk containment: You are preventing catastrophic single-trade blowouts by keeping maximum losses capped.")

        # 3. Execution Discipline
        if no_sl_count == 0:
            ai_briefing['strengths'].append("Flawless structural discipline: 100% of your trades utilized a predefined Stop Loss.")
        elif cut_losses_early > 0:
            ai_briefing['strengths'].append(f"Proactive risk management: You successfully manually cut {cut_losses_early} losing trades before they hit maximum Stop Loss.")
        elif hit_tp > 0:
            ai_briefing['strengths'].append(f"Target adherence: You exercised patience and held {hit_tp} trades perfectly to their Take Profit zones.")
        else:
            ai_briefing['strengths'].append("Market engagement: You are actively executing setups and building a statistical base for future optimization.")

        # WEAKNESSES (Always generate 3)
        # 1. SL / TP usage
        if no_sl_count > 0:
            pct = int((no_sl_count / total_count) * 100)
            ai_briefing['weaknesses'].append(f"Naked risk exposure: {pct}% of your trades ({no_sl_count}) lacked a hard Stop Loss, risking severe capital damage.")
        elif no_tp_count > 0:
            ai_briefing['weaknesses'].append(f"Ambiguous exits: {no_tp_count} trades lacked Take Profit orders, leading to discretionary (and often emotional) closures.")
        else:
            ai_briefing['weaknesses'].append("Rigid parameters: While using SL/TP is great, ensure your bracket ranges are dynamically adapting to daily market volatility.")

        # 2. Holding Winners / PnL ratio
        if missed_tp > hit_tp and missed_tp > 0:
            ai_briefing['weaknesses'].append(f"Profit anxiety: You exited {missed_tp} winning trades early, leaving significant money on the table before hitting Take Profit.")
        elif avg_win <= avg_loss and avg_win > 0:
            ai_briefing['weaknesses'].append(f"Inverted risk profile: Your average loss (${avg_loss:.2f}) is neutralizing or exceeding your average win (${avg_win:.2f}).")
        elif global_stats['profit_factor'] < 1.0:
            ai_briefing['weaknesses'].append(f"Negative expectancy (PF: {global_stats['profit_factor']}). Your gross losses are bleeding out your gross profits.")
        else:
            ai_briefing['weaknesses'].append("Scaling inefficiency: You are profitable, but likely not maximizing position sizing on your highest probability setups.")

        # 3. Accuracy / Misc
        if global_stats['win_rate'] < 45:
            ai_briefing['weaknesses'].append(f"Entry timing bleed: A {global_stats['win_rate']}% win rate indicates you are frequently getting chopped out or entering prematurely.")
        elif cut_losses_early == 0 and total_losses > 0:
            ai_briefing['weaknesses'].append(f"Passive loss management: You let all {total_losses} losing trades run to full completion without attempting to cut them early when thesis invalidated.")
        else:
            ai_briefing['weaknesses'].append("Trade frequency dilution: Ensure you are not overtrading and diluting your core edge with sub-optimal, low-conviction setups.")

        # ADVICE (Always generate 3 based on above)
        if no_sl_count > 0:
            ai_briefing['advice'].append("Implement a hard rule: No trade is entered without a Stop Loss. A single black swan event could liquidate the account.")
        elif no_tp_count > 0:
            ai_briefing['advice'].append("Define your exit liquidity before you enter. Use Limit orders to take profit instead of relying on manual market execution.")
        else:
            ai_briefing['advice'].append("Review your average hold times. See if letting trades breathe 10% longer mathematically improves your overall Profit Factor.")

        if missed_tp > 0:
            ai_briefing['advice'].append("Trust your pre-market analysis. Stop micromanaging active winners—let the price action play out to your initial target.")
        elif avg_loss >= avg_win and avg_loss > 0:
            ai_briefing['advice'].append("You must restore asymmetry. Either cut your losing trades in half as soon as structure breaks, or double your Take Profit distances.")
        else:
            ai_briefing['advice'].append("Your risk/reward is healthy. Focus on cautiously sizing up your A+ setups while maintaining the same psychological detachment.")

        if global_stats['win_rate'] < 45:
            ai_briefing['advice'].append("Wait for confirmation. Stop trying to predict the exact bottom/top; wait for the trend to clearly shift before entering.")
        elif total_losses > 0 and cut_losses_early == 0:
            ai_briefing['advice'].append("If a trade immediately invalidates your thesis, manually exit. You don't always have to wait for the hard Stop Loss to be hit.")
        else:
            ai_briefing['advice'].append("Maintain strict structural discipline. Your metrics are solid—do not let overconfidence lead to size or frequency bloat.")
            
    else:
        ai_briefing['strengths'].append("Clean slate. Your trading journal is ready to track data.")
        ai_briefing['strengths'].append("Capital preserved. You haven't taken any uncalculated risks yet.")
        ai_briefing['strengths'].append("System initialized. The AI is standing by to process your first execution.")
        
        ai_briefing['weaknesses'].append("Insufficient data to calculate drawdowns or structural flaws.")
        ai_briefing['weaknesses'].append("No win rate or profit factor established yet.")
        ai_briefing['weaknesses'].append("Behavioral patterns cannot be mapped without trade entries.")
        
        ai_briefing['advice'].append("Import your CSV trades or sync your live broker to generate your personalized AI behavioral briefing.")
        ai_briefing['advice'].append("Define your trading strategy rules before placing your first live trade.")
        ai_briefing['advice'].append("Start with small position sizes to build a statistical baseline of your performance.")

    # --- 4. AJAX HANDLING (CALENDAR DATA REQUEST) ---
    if request.headers.get('x-requested-with') == 'XMLHttpRequest' and request.GET.get('action') == 'get_calendar':
        target_year = int(request.GET.get('year', timezone.now().year))
        target_month = int(request.GET.get('month', timezone.now().month))
        
        cal = calendar.Calendar(firstweekday=6)
        month_days_raw = list(cal.itermonthdays(target_year, target_month))
        
        json_days = []
        for day_num in month_days_raw:
            if day_num == 0:
                json_days.append({'day': '', 'has_data': False})
            else:
                d_str = f"{target_year}-{target_month:02d}-{day_num:02d}"
                day_data = daily_map.get(d_str)
                if day_data:
                    json_days.append({
                        'day': day_num, 'has_data': True, 
                        'pnl': day_data['pnl'], 'count': day_data['count'],
                        'url': f"/daily-trades/{target_year}/{target_month}/{day_num}/"
                    })
                else:
                    json_days.append({'day': day_num, 'has_data': False, 'pnl': 0, 'count': 0, 'url': '#'})

        # --- CALCULATE MONTHLY STATS (AJAX) ---
        month_trades = [t for t in all_processed_trades if t['sort_date'].year == target_year and t['sort_date'].month == target_month]
        
        # 1. Profit Factor (Normal - Based on Trades)
        m_gross_win = sum([t['profit'] for t in month_trades if t['profit'] > 0])
        m_gross_loss = sum([abs(t['profit']) for t in month_trades if t['profit'] <= 0])
        m_pf = round(m_gross_win / m_gross_loss, 2) if m_gross_loss > 0 else (99.9 if m_gross_win > 0 else 0)

        # 2. Win Rate (Based on Green/Red Days)
        month_prefix = f"{target_year}-{target_month:02d}"
        month_daily_pnls = [v['pnl'] for k, v in daily_map.items() if k.startswith(month_prefix)]
        green_days = len([p for p in month_daily_pnls if p > 0])
        total_active_days = len(month_daily_pnls)
        
        m_wr = int((green_days / total_active_days) * 100) if total_active_days > 0 else 0

        return JsonResponse({
            'status': 'success',
            'days': json_days,
            'stats': {
                'count': total_active_days, # Days Traded
                'win_rate': m_wr,           # Day-based Win Rate
                'pf': m_pf,                 # Trade-based Profit Factor
                'pnl': sum([t['profit'] for t in month_trades])
            }
        })

    # --- 5. INITIAL PAGE LOAD RENDER ---
    target_year = int(request.GET.get('year', timezone.now().year))
    target_month = int(request.GET.get('month', timezone.now().month))
    
    # --- CALCULATE MONTHLY STATS (Initial) ---
    month_trades = [t for t in all_processed_trades if t['sort_date'].year == target_year and t['sort_date'].month == target_month]
    
    # 1. Profit Factor (Normal - Based on Trades)
    m_gross_win = sum([t['profit'] for t in month_trades if t['profit'] > 0])
    m_gross_loss = sum([abs(t['profit']) for t in month_trades if t['profit'] <= 0])
    
    # 2. Win Rate (Based on Green/Red Days)
    month_prefix = f"{target_year}-{target_month:02d}"
    month_daily_pnls = [v['pnl'] for k, v in daily_map.items() if k.startswith(month_prefix)]
    green_days = len([p for p in month_daily_pnls if p > 0])
    total_active_days = len(month_daily_pnls)
    
    monthly_stats = {
        'count': total_active_days,
        'win_rate': int((green_days / total_active_days) * 100) if total_active_days > 0 else 0, # Day-based
        'profit_factor': round(m_gross_win / m_gross_loss, 2) if m_gross_loss > 0 else (99.9 if m_gross_win > 0 else 0), # Trade-based
        'pnl': sum([t['profit'] for t in month_trades])
    }

    cal = calendar.Calendar(firstweekday=6)
    month_days_raw = list(cal.itermonthdays(target_year, target_month))
    for day_num in month_days_raw:
        if day_num == 0: monthly_days.append({'day': '', 'has_data': False})
        else:
            d_str = f"{target_year}-{target_month:02d}-{day_num:02d}"
            day_data = daily_map.get(d_str)
            if day_data: monthly_days.append({'day': day_num, 'has_data': True, 'pnl': day_data['pnl'], 'count': day_data['count']})
            else: monthly_days.append({'day': day_num, 'has_data': False, 'pnl': 0, 'count': 0})

    return render(request, 'dashboard.html', {
        'accounts': user_accounts, 
        'current_account': current_account, 
        'stats': global_stats, # Top Gauges (All Time)
        'monthly_stats': monthly_stats, # Calendar Header (Specific Month)
        'positions': positions, 
        'equity_curve': equity_curve, 
        'drawdown_curve': drawdown_curve, 
        'monthly_days': monthly_days, 
        'is_connected': is_connected, 
        'balance': live_balance, 
        'buying_power': buying_power, 
        'today_date': timezone.now(), 
        'target_year': target_year, 
        'target_month': target_month,
        'ai_briefing': ai_briefing  # Passed into context
    })
# --- 4. DAILY TRADES VIEW ---
@login_required
def daily_trades(request, year, month, day):
    current_account, user_accounts = get_active_account(request)
    target_date_str = f"{year}-{month:02d}-{day:02d}"
    day_trades = []
    
    # --- SAFE FLOAT HELPER ---
    def _safe_float(val):
        try:
            if val is None or str(val).strip() in ['', 'None', 'null']: 
                return 0.0
            return float(val)
        except (ValueError, TypeError):
            return 0.0

    # API FETCH
    if current_account and current_account.snaptrade_user_id:
        try:
            client = TradeSmartCloud()
            ST_UID = current_account.snaptrade_user_id
            ST_SECRET = current_account.user_secret
            cloud_accounts = client.get_accounts(ST_UID, ST_SECRET)
            if cloud_accounts:
                acc_id = cloud_accounts[0]['id']
                # DEEP FETCH: 365 Days
                orders_res = client.api.account_information.get_user_account_orders(ST_UID, ST_SECRET, acc_id, state='all', days=365)
                all_orders = orders_res.body if isinstance(orders_res.body, list) else []
                
                chron_orders = sorted(all_orders, key=lambda x: x.get('time_executed') or x.get('time_placed') or '0000-00-00', reverse=False)
                inventory = {} 

                for order in chron_orders:
                    raw_date = (order.get('time_executed') or order.get('time_placed') or order.get('time_updated') or order.get('execution_time'))
                    trade_date_str = "0000-00-00"
                    final_time_str = "--:--:--"
                    trade_open_datetime = None
                    trade_close_datetime = None
                    
                    if raw_date:
                        try:
                            if str(raw_date).isdigit():
                                dt = datetime.fromtimestamp(int(raw_date) / 1000.0)
                                trade_date_str = str(dt.date())
                                final_time_str = dt.strftime("%H:%M:%S")
                                trade_close_datetime = dt # Approximation for API
                            else:
                                trade_date_str = str(raw_date)[:10]
                                try: 
                                    dt = datetime.fromisoformat(str(raw_date).replace('Z', '+00:00'))
                                    final_time_str = dt.strftime("%H:%M:%S")
                                    trade_close_datetime = dt
                                except: final_time_str = str(raw_date)[11:19]
                        except: trade_date_str = str(raw_date)[:10]

                    sym_obj = order.get('universal_symbol') or order.get('symbol')
                    symbol = extract_clean_symbol(sym_obj)
                    action = order.get('action', '').upper()
                    units = _safe_float(order.get('filled_quantity') or order.get('total_quantity'))
                    price = _safe_float(order.get('execution_price') or order.get('price'))
                    trade_pnl = 0.0; trade_closed = False; closing_price = price; opening_price = 0.0

                    if symbol not in inventory: inventory[symbol] = {'qty': 0.0, 'avg_price': 0.0, 'open_time': trade_close_datetime}
                    current_qty = inventory[symbol]['qty']; avg_entry = inventory[symbol]['avg_price']

                    if action == 'BUY':
                        if current_qty < 0:
                            opening_price = avg_entry; cover_qty = min(abs(current_qty), units)
                            trade_pnl = (avg_entry - price) * cover_qty; trade_closed = True
                            trade_open_datetime = inventory[symbol]['open_time']
                            inventory[symbol]['qty'] += cover_qty
                            if inventory[symbol]['qty'] == 0: inventory[symbol]['avg_price'] = 0.0
                            remainder = units - cover_qty
                            if remainder > 0: 
                                inventory[symbol]['qty'] += remainder; inventory[symbol]['avg_price'] = price
                                inventory[symbol]['open_time'] = trade_close_datetime
                        else:
                            total_val = (current_qty * avg_entry) + (units * price); new_total_qty = current_qty + units
                            if new_total_qty > 0: inventory[symbol]['avg_price'] = total_val / new_total_qty
                            inventory[symbol]['qty'] = new_total_qty
                            if current_qty == 0: inventory[symbol]['open_time'] = trade_close_datetime
                    elif action == 'SELL':
                        if current_qty > 0:
                            opening_price = avg_entry; close_qty = min(current_qty, units)
                            trade_pnl = (price - avg_entry) * close_qty; trade_closed = True
                            trade_open_datetime = inventory[symbol]['open_time']
                            inventory[symbol]['qty'] -= close_qty
                            if inventory[symbol]['qty'] == 0: inventory[symbol]['avg_price'] = 0.0
                            remainder = units - close_qty
                            if remainder > 0: 
                                inventory[symbol]['qty'] -= remainder; inventory[symbol]['avg_price'] = price
                                inventory[symbol]['open_time'] = trade_close_datetime 
                        else:
                            total_val = (abs(current_qty) * avg_entry) + (units * price); new_total_abs_qty = abs(current_qty) + units
                            if new_total_abs_qty > 0: inventory[symbol]['avg_price'] = total_val / new_total_abs_qty
                            inventory[symbol]['qty'] -= units
                            if current_qty == 0: inventory[symbol]['open_time'] = trade_close_datetime

                    if trade_closed and trade_date_str == target_date_str:
                        tid = generate_trade_id(order)
                        day_trades.append({
                            'trade_id': tid, 'ticket': tid, 'symbol': symbol, 'direction': action, 
                            'type': action, 'volume': units, 'qty': units, 'lot_size': units, 
                            'open_price': round(opening_price, 2), 'close_price': round(closing_price, 2), 
                            'profit': round(trade_pnl, 2), 'time': final_time_str,
                            'open_time': trade_open_datetime, 'close_time': trade_close_datetime
                        })

        except Exception as e:
            print(f"Daily Trades Error: {e}")
    
    # DB FETCH (MANUAL)
    else:
        target_dt = datetime.strptime(target_date_str, "%Y-%m-%d").date()
        db_trades_qs = Trade.objects.filter(
            account=current_account,
            open_time__date=target_dt
        ).prefetch_related('notes').exclude(symbol='BALANCE').order_by('open_time')

        for t in db_trades_qs:
            day_trades.append({
                'trade_id': t.trade_id,
                'ticket': t.trade_id,
                'symbol': t.symbol,
                'direction': t.direction,
                'type': t.direction,
                'volume': _safe_float(t.lot_size),
                'qty': _safe_float(t.lot_size),
                'lot_size': _safe_float(t.lot_size),
                'open_price': _safe_float(t.open_price),
                'close_price': _safe_float(t.close_price),
                'profit': _safe_float(t.profit),
                'time': t.open_time.strftime("%H:%M:%S") if t.open_time else "--:--:--",
                'open_time': t.open_time,
                'close_time': t.close_time
            })

    # Sort chronologically for sequence tracking
    day_trades.sort(key=lambda x: str(x.get('time')), reverse=False)

    # --- CALCULATE DAILY STATS ---
    stats = {
        'net_pnl': 0.00,
        'win_rate': 0,
        'profit_factor': 0.00,
        'total_trades': len(day_trades)
    }

    if day_trades:
        total_pnl = sum(t['profit'] for t in day_trades)
        wins = [t['profit'] for t in day_trades if t['profit'] > 0]
        losses = [abs(t['profit']) for t in day_trades if t['profit'] <= 0]
        
        stats['net_pnl'] = round(total_pnl, 2)
        
        if stats['total_trades'] > 0:
            stats['win_rate'] = round((len(wins) / stats['total_trades']) * 100)
        
        gross_profit = sum(wins)
        gross_loss = sum(losses)
        
        if gross_loss > 0:
            stats['profit_factor'] = round(gross_profit / gross_loss, 2)
        elif gross_profit > 0:
            stats['profit_factor'] = 99.99

    # =========================================================
    # MODULE A: PURE ML ENSEMBLE (K-Means Clustering + XGBoost + HMM)
    # =========================================================
    behavior = {
        'revenge_flag': False, 'revenge_msg': '', 'revenge_trades': [],
        'overconfidence_flag': False, 'overconfidence_msg': '', 'overconfidence_trades': [],
        'fear_flag': False, 'fear_msg': '', 'fear_trades': [],
        'tilt_flag': False, 'tilt_msg': '',
        'xgb_flag': False, 'xgb_msg': '', 'xgb_trades': []
    }

    if len(day_trades) >= 3: 
        try:
            import numpy as np
            from sklearn.cluster import KMeans
            from sklearn.preprocessing import StandardScaler
            import xgboost as xgb
            from hmmlearn.hmm import GaussianHMM

            # --- 1. FEATURE ENGINEERING ---
            current_streak = 0
            last_close_time = None
            
            X_today = []
            for t in day_trades:
                time_diff_sec = 3600 # Default 1 hour
                if last_close_time and t['open_time']:
                    try: time_diff_sec = abs((t['open_time'] - last_close_time).total_seconds())
                    except TypeError: time_diff_sec = 3600
                time_diff_sec = min(time_diff_sec, 86400) # Cap at 24h

                # Features: [Lot Size, PnL, Streak, Time Elapsed]
                X_today.append([t['lot_size'], t['profit'], current_streak, time_diff_sec])
                
                if t['profit'] < 0: current_streak -= 1
                elif t['profit'] > 0: current_streak += 1
                else: current_streak = 0
                    
                if t['close_time']: last_close_time = t['close_time']
            
            X_today = np.array(X_today)

            # --- 2. HISTORICAL BASELINE CALCULATION ---
            baseline_trades = Trade.objects.filter(account=current_account).exclude(symbol='BALANCE').prefetch_related('notes').order_by('open_time')
            baseline_list = list(baseline_trades)[-250:] # Grab last 250 trades
            
            if len(baseline_list) > 10:
                X_baseline = []
                y_xgb_labels = [] 
                b_current_streak = 0
                b_last_close = None
                
                for bt in baseline_list:
                    b_lot = _safe_float(bt.lot_size)
                    b_pnl = _safe_float(bt.profit)
                    
                    b_time_diff_sec = 3600
                    if b_last_close and bt.open_time:
                        try: b_time_diff_sec = abs((bt.open_time - b_last_close).total_seconds())
                        except: pass
                    b_time_diff_sec = min(b_time_diff_sec, 86400)

                    X_baseline.append([b_lot, b_pnl, b_current_streak, b_time_diff_sec])
                    
                    # Target Extraction for XGBoost (Did they break rules?)
                    label = -1 # Default: Unlabeled
                    for note in bt.notes.all():
                        content = note.content.upper()
                        if "[CHECKLIST REVIEW" in content:
                            if "YES" in content or "INEVITABLE" in content:
                                label = 0 # Followed Rules
                            elif "NO" in content or "PSYCHOLOGICAL" in content:
                                label = 1 # Broke Rules
                    y_xgb_labels.append(label)

                    if b_pnl < 0: b_current_streak -= 1
                    elif b_pnl > 0: b_current_streak += 1
                    else: b_current_streak = 0
                    if bt.close_time: b_last_close = bt.close_time
                
                X_baseline = np.array(X_baseline)
                y_xgb_labels = np.array(y_xgb_labels)

                # ==========================================
                # LAYER 1: K-MEANS CLUSTERING (Unsupervised Profiling)
                # ==========================================
                scaler = StandardScaler()
                X_base_scaled = scaler.fit_transform(X_baseline)
                X_today_scaled = scaler.transform(X_today)

                kmeans = KMeans(n_clusters=4, random_state=42, n_init='auto')
                kmeans.fit(X_base_scaled)

                # Extract the mathematical center of each personality profile
                centroids = scaler.inverse_transform(kmeans.cluster_centers_)
                # Feature Index: 0=Lot, 1=PnL, 2=Streak, 3=TimeElapsed

                # Dynamically label the clusters based on mathematical extremes
                overconf_cluster = np.argmax(centroids[:, 0]) # Highest average lot size
                revenge_cluster = np.argmin(centroids[:, 2])  # Worst average streak before entry
                
                # Prevent overlap if the highest volume cluster is ALSO the worst streak cluster
                if overconf_cluster == revenge_cluster:
                    revenge_cluster = np.argsort(centroids[:, 2])[1] # Pick the second worst streak

                remaining = [i for i in range(4) if i not in [overconf_cluster, revenge_cluster]]
                # Fear is characterized by small volume and cutting profits fast
                fear_cluster = min(remaining, key=lambda i: centroids[i, 0])

                # Predict today's trades into these learned profiles
                cluster_predictions = kmeans.predict(X_today_scaled)
                
                median_lot = np.median(X_baseline[:, 0])
                median_pnl = np.median(X_baseline[:, 1])

                for i, pred in enumerate(cluster_predictions):
                    trade = day_trades[i]
                    tid = f"#{trade['trade_id'][:6]}"
                    pnl = X_today[i][1]
                    lot = X_today[i][0]
                    
                    # Add safety bounds so normal trades aren't flagged just because they belong to a cluster
                    if pred == revenge_cluster and pnl < 0:
                        behavior['revenge_flag'] = True
                        if tid not in behavior['revenge_trades']: behavior['revenge_trades'].append(tid)
                        
                    elif pred == overconf_cluster and lot > median_lot:
                        behavior['overconfidence_flag'] = True
                        if tid not in behavior['overconfidence_trades']: behavior['overconfidence_trades'].append(tid)
                        
                    elif pred == fear_cluster and pnl > 0 and pnl <= abs(median_pnl):
                        behavior['fear_flag'] = True
                        if tid not in behavior['fear_trades']: behavior['fear_trades'].append(tid)

                # Format Messages
                if behavior['overconfidence_flag']: behavior['overconfidence_msg'] = f"Trades {', '.join(behavior['overconfidence_trades'])} matched your 'High-Leverage' ML cluster. Risk of overconfidence detected."
                if behavior['revenge_flag']: behavior['revenge_msg'] = f"Trades {', '.join(behavior['revenge_trades'])} matched your 'Tilt/Loss' ML cluster (Negative streak + rapid entry). Classic revenge signature."
                if behavior['fear_flag']: behavior['fear_msg'] = f"Trades {', '.join(behavior['fear_trades'])} matched your 'Micro-Scalp' ML cluster. The AI suspects fear-based early exits."

                # ==========================================
                # LAYER 2: HIDDEN MARKOV MODEL (The Psychologist)
                # ==========================================
                try:
                    # Train to find 2 hidden mental states based on PnL and Time
                    hmm_model = GaussianHMM(n_components=2, covariance_type="diag", n_iter=100, random_state=42)
                    hmm_model.fit(X_baseline)
                    
                    # Figure out which of the 2 states represents "Tilt" (State with the lowest average PnL)
                    tilt_state_index = np.argmin(hmm_model.means_[:, 1]) 
                    
                    # Predict the mental state of the user for today's sequence
                    today_states = hmm_model.predict(X_today)
                    
                    # If the majority of the last 3 trades today are in the Tilt state
                    recent_states = today_states[-3:]
                    if list(recent_states).count(tilt_state_index) >= 2:
                        behavior['tilt_flag'] = True
                        behavior['tilt_msg'] = "HMM Sequence Analyzer detected a massive shift in your execution pattern. You are currently in a high-variance TILT state. Stop trading."
                except Exception as hmm_err:
                    print(f"HMM Error: {hmm_err}")


                # ==========================================
                # LAYER 3: XGBOOST (Supervised Checklist Judge)
                # ==========================================
                valid_idx = np.where(y_xgb_labels != -1)[0]
                if len(valid_idx) > 5 and len(set(y_xgb_labels[valid_idx])) > 1:
                    X_train = X_baseline[valid_idx]
                    y_train = y_xgb_labels[valid_idx]

                    xgb_clf = xgb.XGBClassifier(eval_metric='logloss', random_state=42)
                    xgb_clf.fit(X_train, y_train)

                    proba = xgb_clf.predict_proba(X_today)[:, 1] 
                    for i, risk in enumerate(proba):
                        if risk >= 0.75: # 75% confidence
                            trade = day_trades[i]
                            behavior['xgb_flag'] = True
                            if f"#{trade['trade_id'][:6]} ({int(risk*100)}% risk)" not in behavior['xgb_trades']:
                                behavior['xgb_trades'].append(f"#{trade['trade_id'][:6]} ({int(risk*100)}% risk)")
                            
                    if behavior['xgb_flag']:
                        behavior['xgb_msg'] = f"XGBoost analyzed your past Checklist rules. High probability you broke your rules today on: {', '.join(behavior['xgb_trades'])}."

        except ImportError:
            # We intercept missing libraries and output it directly to the UI
            behavior['tilt_flag'] = True
            behavior['tilt_msg'] = "ML Engine Offline. Please install required libraries by running: pip install scikit-learn xgboost numpy hmmlearn"
        except Exception as e:
            # We intercept data math errors and output it to the UI
            behavior['tilt_flag'] = True
            behavior['tilt_msg'] = f"ML Engine Error: {str(e)}. Your baseline might need more historical data."

    # Sort reverse chronological for the UI display
    day_trades.sort(key=lambda x: str(x.get('time')), reverse=True)

    return render(request, 'daily_trades.html', {
        'trades': day_trades, 
        'date_str': target_date_str, 
        'stats': stats, 
        'behavior': behavior,
        'accounts': user_accounts, 
        'current_account': current_account
    })
# --- 5. JOURNAL ---
@login_required
def journal(request):
    current_account, user_accounts = get_active_account(request)
    journal_trades = []
    
    # --- DEDUPLICATION SETS ---
    existing_ids = set()
    existing_signatures = set() # Signature = Symbol_Timestamp_Lots

    # Defaults
    stats = {
        'net_pnl': 0.00, 'win_rate': 0, 'profit_factor': 0.00,
        'total_trades': 0, 'avg_win': 0.00, 'avg_loss': 0.00
    }

    # =========================================================
    # SOURCE 1: LOCAL DATABASE (Synced & Manual Trades)
    # =========================================================
    if current_account:
        try:
            # OPTIMIZED: Use .values() to fetch only required fields as dicts
            db_trades_qs = Trade.objects.filter(account=current_account)\
                .exclude(symbol='BALANCE')\
                .exclude(strategy_tag="Live Monitor Log")\
                .order_by('-open_time')\
                .values(
                    'trade_id', 'symbol', 'open_time', 'direction', 
                    'lot_size', 'open_price', 'close_price', 'profit', 'strategy_tag'
                )

            for t in db_trades_qs:
                # 1. Store ID for deduplication against API trades
                if t['trade_id']:
                    existing_ids.add(t['trade_id'])
                
                # 2. Store Signature for deduplication
                if t['open_time']:
                    sig = f"{t['symbol']}_{int(t['open_time'].timestamp())}_{float(t['lot_size'])}"
                    existing_signatures.add(sig)

                # 3. Append dictionary to list
                journal_trades.append({
                    'trade_id': t['trade_id'],
                    'id': t['trade_id'],
                    'symbol': t['symbol'],
                    'open_time': t['open_time'],
                    'full_date': t['open_time'].strftime("%Y-%m-%d") if t['open_time'] else "",
                    'date': t['open_time'].strftime("%H:%M") if t['open_time'] else "",
                    'direction': t['direction'],
                    'type': t['direction'],
                    'lot_size': float(t['lot_size']),
                    'lots': float(t['lot_size']),
                    'open_price': float(t['open_price']),
                    'entry': float(t['open_price']),
                    'close_price': float(t['close_price']),
                    'exit': float(t['close_price']),
                    'profit': float(t['profit']),
                    'pnl': float(t['profit']),
                    'strategy': t['strategy_tag'] or 'Manual',
                    'source': 'Database'
                })
        except Exception as e:
            print(f"Journal DB Error: {e}")

    # =========================================================
    # SOURCE 2: SNAPTRADE API (Live / Unsynced Trades)
    # =========================================================
    if current_account and current_account.snaptrade_user_id:
        print("\n\n" + "="*50)
        print("--- [DEBUG] SNAPTRADE API FETCH START ---")
        try:
            client = TradeSmartCloud()
            ST_UID = current_account.snaptrade_user_id
            ST_SECRET = current_account.user_secret
            
            print(f"[DEBUG] Attempting to fetch accounts for UID: {ST_UID}")
            cloud_accounts = client.get_accounts(ST_UID, ST_SECRET)
            print(f"[DEBUG] Cloud Accounts Returned: {cloud_accounts}")
            
            if cloud_accounts:
                acc_id = cloud_accounts[0]['id']
                print(f"[DEBUG] Fetching orders for Account ID: {acc_id} (Deep Fetch: 365 Days)")
                
                orders_res = client.api.account_information.get_user_account_orders(ST_UID, ST_SECRET, acc_id, state='all', days=365)
                
                all_orders = orders_res.body if hasattr(orders_res, 'body') and isinstance(orders_res.body, list) else []
                print(f"[DEBUG] Total raw orders extracted: {len(all_orders)}")
                
                if len(all_orders) > 0:
                    print(f"[DEBUG] Displaying Sample Order [0]: {all_orders[0]}")
                
                # Sort FIFO
                chron_orders = sorted(all_orders, key=lambda x: x.get('time_executed') or x.get('time_placed') or '0000-00-00', reverse=False)
                inventory = {} 

                for order in chron_orders:
                    try:
                        # [DATE PARSING]
                        raw_date = (order.get('time_executed') or order.get('time_placed') or order.get('time_updated') or order.get('execution_time'))
                        trade_dt = timezone.now(); trade_date_str = "0000-00-00"; final_time_str = "--:--:--"
                        
                        if raw_date:
                            try:
                                if str(raw_date).isdigit(): 
                                    dt = datetime.fromtimestamp(int(raw_date) / 1000.0)
                                    trade_dt = make_aware(dt)
                                    trade_date_str = str(dt.date())
                                    final_time_str = dt.strftime("%H:%M:%S")
                                else: 
                                    trade_date_str = str(raw_date)[:10]
                                    trade_dt = datetime.fromisoformat(str(raw_date).replace('Z', '+00:00'))
                            except: trade_date_str = str(raw_date)[:10]

                        # [DATA EXTRACTION]
                        sym_obj = order.get('universal_symbol') or order.get('symbol'); symbol = extract_clean_symbol(sym_obj)
                        action = order.get('action', '').upper()
                        units = float(order.get('filled_quantity') or order.get('total_quantity') or 0.0)
                        price = float(order.get('execution_price') or order.get('price') or 0.0)
                        
                        trade_pnl = 0.0; trade_closed = False; closing_price = price; opening_price = 0.0

                        if symbol not in inventory: inventory[symbol] = {'qty': 0.0, 'avg_price': 0.0}
                        current_qty = inventory[symbol]['qty']; avg_entry = inventory[symbol]['avg_price']

                        if action == 'BUY':
                            if current_qty < 0: # Closing Short
                                opening_price = avg_entry; cover_qty = min(abs(current_qty), units)
                                trade_pnl = (avg_entry - price) * cover_qty; trade_closed = True
                                inventory[symbol]['qty'] += cover_qty
                                if inventory[symbol]['qty'] == 0: inventory[symbol]['avg_price'] = 0.0
                                if units - cover_qty > 0: inventory[symbol]['qty'] += (units - cover_qty); inventory[symbol]['avg_price'] = price 
                            else: # Opening Long
                                total_val = (current_qty * avg_entry) + (units * price); new_total_qty = current_qty + units
                                if new_total_qty > 0: inventory[symbol]['avg_price'] = total_val / new_total_qty
                                inventory[symbol]['qty'] = new_total_qty
                        elif action == 'SELL':
                            if current_qty > 0: # Closing Long
                                opening_price = avg_entry; close_qty = min(current_qty, units)
                                trade_pnl = (price - avg_entry) * close_qty; trade_closed = True
                                inventory[symbol]['qty'] -= close_qty
                                if inventory[symbol]['qty'] == 0: inventory[symbol]['avg_price'] = 0.0
                                if units - close_qty > 0: inventory[symbol]['qty'] -= (units - close_qty); inventory[symbol]['avg_price'] = price 
                            else: # Opening Short
                                total_val = (abs(current_qty) * avg_entry) + (units * price); new_total_abs_qty = abs(current_qty) + units
                                if new_total_abs_qty > 0: inventory[symbol]['avg_price'] = total_val / new_total_abs_qty
                                inventory[symbol]['qty'] -= units

                        if trade_closed:
                            tid = generate_trade_id(order)
                            
                            # --- CRITICAL DEDUPLICATION ---
                            # Check ID
                            if tid in existing_ids: continue 
                            
                            # Check Signature
                            current_sig = f"{symbol}_{int(trade_dt.timestamp())}_{units}"
                            if current_sig in existing_signatures: continue
                            # ------------------------------

                            direction = 'LONG' if action == 'SELL' else 'SHORT'
                            
                            journal_trades.append({
                                'trade_id': tid,
                                'id': tid, 
                                'open_time': trade_dt, 
                                'date': final_time_str,
                                'full_date': trade_date_str,
                                'symbol': symbol, 
                                'type': direction,
                                'direction': direction,
                                'lots': units,
                                'lot_size': units,
                                'entry': round(opening_price, 2),
                                'open_price': round(opening_price, 2),
                                'exit': round(closing_price, 2),
                                'close_price': round(closing_price, 2),
                                'pnl': round(trade_pnl, 2),
                                'profit': round(trade_pnl, 2),
                                'strategy': 'Auto-Sync',
                                'source': 'API'
                            })
                            print(f"[DEBUG] Successfully added trade for symbol {symbol} to journal.")
                    
                    except Exception as inner_e:
                        print(f"[DEBUG] Failed to parse specific order: {inner_e}")
                        
        except Exception as e: 
            print(f"[DEBUG] CRITICAL Journal API Error: {e}")
            traceback.print_exc()
        
        print("--- [DEBUG] SNAPTRADE API FETCH END ---")
        print("="*50 + "\n\n")

    # =========================================================
    # 3. FILTERS & SORTING
    # =========================================================
    try:
        journal_trades.sort(key=lambda x: x.get('open_time') if x.get('open_time') else timezone.now(), reverse=True)
    except: pass 

    q = request.GET.get('q', '').strip().upper()
    side = request.GET.get('side', 'all').lower()
    outcome = request.GET.get('outcome', 'all').lower()
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')

    if q: journal_trades = [t for t in journal_trades if q in t['symbol'].upper()]
    if side != 'all':
        target_sides = ['buy', 'long'] if side in ['buy', 'long'] else ['sell', 'short']
        journal_trades = [t for t in journal_trades if str(t['type']).lower() in target_sides or str(t['direction']).lower() in target_sides]
    if outcome != 'all':
        if outcome == 'win': journal_trades = [t for t in journal_trades if t['pnl'] > 0]
        elif outcome == 'loss': journal_trades = [t for t in journal_trades if t['pnl'] <= 0]
    if start_date and end_date:
        journal_trades = [t for t in journal_trades if start_date <= t['full_date'] <= end_date]

    # =========================================================
    # 4. CALCULATE STATS
    # =========================================================
    if journal_trades:
        total_pnl = sum(t['pnl'] for t in journal_trades)
        wins = [t['pnl'] for t in journal_trades if t['pnl'] > 0]
        losses = [abs(t['pnl']) for t in journal_trades if t['pnl'] <= 0]
        
        stats['net_pnl'] = round(total_pnl, 2)
        stats['total_trades'] = len(journal_trades)
        if stats['total_trades'] > 0:
            stats['win_rate'] = round((len(wins) / stats['total_trades']) * 100)
        
        gross_profit = sum(wins)
        gross_loss = sum(losses)
        if gross_loss > 0: stats['profit_factor'] = round(gross_profit / gross_loss, 2)
        elif gross_profit > 0: stats['profit_factor'] = 99.99
            
        if wins: stats['avg_win'] = round(sum(wins) / len(wins), 2)
        if losses: stats['avg_loss'] = round(sum(losses) / len(losses), 2)

    # =========================================================
    # 5. PAGINATION (Prevents Crash on Large Lists)
    # =========================================================
    paginator = Paginator(journal_trades, 50) # Show 50 trades per page
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, 'journal.html', {
        'journal_trades': page_obj,  # Pass the page object instead of full list
        'stats': stats, 
        'current_account': current_account, 
        'accounts': user_accounts
    })
# --- 6. LIVE MONITOR ---
@login_required
def live_monitor(request):
    current_account, user_accounts = get_active_account(request)
    
    if not current_account or not current_account.snaptrade_user_id:
        return render(request, 'live_monitor_locked.html', {
            'accounts': user_accounts, 
            'current_account': current_account
        })

    clean_trades = []
    floating_pnl = 0.00
    is_connected = False
    
    is_ajax_poll = (request.headers.get('x-requested-with') == 'XMLHttpRequest' and request.GET.get('action') == 'get_pnl')

    if request.method == 'POST':
        symbol = request.POST.get('symbol')
        qty = request.POST.get('qty') 
        note_content = request.POST.get('note')
        emotion = request.POST.get('emotion')
        
        if symbol and note_content:
            try:
                # 1. Generate unique ID using timestamp
                timestamp_key = int(time.time())
                unique_live_id = f"LIVE_{request.user.id}_{symbol}_{timestamp_key}"
                
                # 2. Create Placeholder Trade (With REQUIRED 'user' field)
                trade_obj, _ = Trade.objects.get_or_create(
                    trade_id=unique_live_id,
                    defaults={
                        'user': request.user,  # <--- ADDED THIS BACK (Required by DB)
                        'account': current_account,
                        'symbol': symbol,
                        'lot_size': float(qty) if qty else 0.0,
                        'strategy_tag': "Live Monitor Log", # Tagged for filtering
                        'source_platform': "Live Monitor",
                        'open_time': timezone.now()
                    }
                )
                
                # 3. Save Note
                TradeNote.objects.create(
                    trade=trade_obj, 
                    content=note_content, 
                    emotion_tag=emotion
                )
                messages.success(request, "Entry logged successfully.")
                return redirect('live_monitor')
            
            except Exception as e:
                print(f"Error saving live note: {e}")
                messages.error(request, "Could not save note.")

    if current_account and current_account.snaptrade_user_id:
        try:
            client = TradeSmartCloud(); ST_UID = current_account.snaptrade_user_id; ST_SECRET = current_account.user_secret
            cloud_accounts = client.get_accounts(ST_UID, ST_SECRET)
            if cloud_accounts:
                is_connected = True; acc_id = cloud_accounts[0]['id']
                portfolio_data = client.fetch_positions(ST_UID, ST_SECRET, acc_id)
                raw_positions = portfolio_data.get('positions', []) if isinstance(portfolio_data, dict) else []
                
                for pos in raw_positions:
                    pnl = float(pos.get('open_pnl', 0) or 0.0); floating_pnl += pnl
                    if not is_ajax_poll:
                        raw_asset = pos.get('symbol')
                        asset_name = extract_clean_symbol(raw_asset)
                        qty = float(pos.get('units', 0) or 0.0)
                        price = float(pos.get('price', 0) or 0.0)
                        clean_trades.append({'name': asset_name, 'qty': qty, 'price': price, 'pnl': pnl})
        except: pass

    if is_ajax_poll: return JsonResponse({'status': 'success', 'pnl': round(floating_pnl, 2)})

    return render(request, 'live_monitor.html', {'open_trades': clean_trades, 'floating_pnl': round(floating_pnl, 2), 'has_active_account': is_connected, 'current_account': current_account, 'accounts': user_accounts})
# --- 7. PROFILE & MANAGEMENT ---
@login_required
def profile_view(request):
    profile, created = UserProfile.objects.get_or_create(user=request.user)
    accounts = TradingAccount.objects.filter(user=request.user)
    
    # --- ADDED THIS LINE ---
    # Required because your sidebar HTML checks "if current_account.snaptrade_user_id"
    current_account, _ = get_active_account(request) 
    
    context = {
        'profile': profile, 
        'accounts': accounts,
        'current_account': current_account # Passed to template
    }
    return render(request, 'profile.html', context)

@login_required
def delete_trading_account(request, account_id):
    # Ensure the account belongs to the logged-in user for security
    account = get_object_or_404(TradingAccount, id=account_id, user=request.user)
    
    if request.method == 'POST':
        # --- 0. INTERNET CONNECTIVITY CHECK ---
        # We must verify connection before attempting remote cleanup
        try:
            # Attempt to connect to Google DNS (8.8.8.8) on port 53 to verify internet access
            socket.create_connection(("8.8.8.8", 53), timeout=3)
        except OSError:
            messages.error(request, "Action blocked: No internet connection detected. You must be online to disconnect an account.")
            return redirect('settings')
        # ---------------------------------------

        broker_name = account.broker_name
        
        # 1. REMOTE CLEANUP (SnapTrade)
        # We do this BEFORE local deletion. If this runs, the "Connection Limit" is freed.
        if account.snaptrade_user_id:
            try:
                client = TradeSmartCloud()
                # Use the helper method (ensure delete_snaptrade_user is added to TradeSmartCloud class)
                success = client.delete_snaptrade_user(account.snaptrade_user_id)
                
                if not success:
                    # Non-blocking warning
                    messages.warning(request, "Account deleted locally, but SnapTrade cleanup failed. Check logs.")
            except Exception as e:
                print(f"SnapTrade Deletion Error: {e}")

        # 2. LOCAL CLEANUP (Database)
        account.delete()

        # 3. SESSION CLEANUP
        # Check if the deleted account was the one currently active in the session
        if str(request.session.get('active_account_id')) == str(account_id):
            if 'active_account_id' in request.session:
                del request.session['active_account_id']

        messages.success(request, f"Successfully disconnected {broker_name}.")
        return redirect('settings') # Redirect back to Profile/Settings page

    # Optional confirmation render, though usually handled via modal/confirm() in settings
    return render(request, 'confirm_delete.html', {'account': account})
# --- 8. TRADE DETAIL (WITH SMART LINKING) ---
def generate_ai_analysis(trade_data, note_content=""):
    """
    Generates Emotional and Strategic insights based on JOURNAL TEXT 
    AND hard technical metrics (Entry, Exit, SL, TP).
    """
    pnl = trade_data.get('profit', 0.0)
    symbol = trade_data.get('symbol', 'UNKNOWN')
    direction = trade_data.get('type', 'LONG')
    
    # Extract Technical Data (Handle None/0 safe)
    entry = float(trade_data.get('entry_price') or 0.0)
    exit_price = float(trade_data.get('exit_price') or 0.0)
    sl = float(trade_data.get('stop_loss') or 0.0)
    tp = float(trade_data.get('take_profit') or 0.0)

    # --- EMOTIONAL ANALYSIS ---
    sentiment = "NEUTRAL"
    emo_desc = "Analysis based on trade metrics."

    # 1. Keyword Dictionaries (Text Priority)
    keywords = {
        'ANXIOUS': ['scared', 'nervous', 'hope', 'pray', 'stress', 'fear', 'too big', 'red', 'worried', 'panic'],
        'EUPHORIC': ['moon', 'easy', 'huge', 'rich', 'yolo', 'all in', 'fomo', 'chasing', 'god mode'],
        'TILTED': ['stupid', 'again', 'revenge', 'back', 'mad', 'hate', 'recover', 'anger', 'market is rigged'],
        'DISCIPLINED': ['plan', 'strategy', 'risk', 'wait', 'patient', 'setup', 'target', 'stop', 'tp', 'sl', 'followed']
    }

    # 2. Text Analysis
    text_sentiment = None
    if note_content:
        text = note_content.lower()
        found_emotions = []
        for emotion, words in keywords.items():
            if any(w in text for w in words):
                found_emotions.append(emotion)
        
        if 'TILTED' in found_emotions: text_sentiment = ("TILTED / REVENGE", "Journal language indicates frustration and anger.")
        elif 'ANXIOUS' in found_emotions: text_sentiment = ("ANXIOUS", "Language suggests fear or uncertainty.")
        elif 'EUPHORIC' in found_emotions: text_sentiment = ("EUPHORIC", "High confidence detected. Beware of overconfidence.")
        elif 'DISCIPLINED' in found_emotions: text_sentiment = ("CALM / FOCUSED", "Professional mindset detected. Thinking in probabilities.")

    # 3. TECHNICAL BEHAVIORAL ANALYSIS (The Update)
    # This overrides text if the math shows clear behavioral patterns
    
    tech_sentiment = "NEUTRAL"
    tech_desc = ""

    if entry > 0 and exit_price > 0:
        # A. CHECK FOR "EARLY EXIT" (Panic Closing)
        # If SL exists, PnL is negative, but we didn't hit SL (we closed manually way before)
        if sl > 0 and pnl < 0:
            loss_dist = abs(entry - sl)
            actual_loss_dist = abs(entry - exit_price)
            # If we closed having taken less than 50% of the planned risk
            if actual_loss_dist < (loss_dist * 0.5):
                tech_sentiment = "NERVOUS / MICROMANAGING"
                tech_desc = "You manually closed a losing trade well before your Stop Loss. This indicates a lack of trust in your plan or fear of loss."

        # B. CHECK FOR "TARGET ADHERENCE" (Discipline)
        # If TP exists and we exited within 0.5% of it
        if tp > 0 and pnl > 0:
            if abs(exit_price - tp) / tp < 0.005:
                tech_sentiment = "DISCIPLINED EXECUTION"
                tech_desc = "Perfect execution. You held the trade all the way to your Take Profit target."

        # C. CHECK FOR "IMPULSIVE ENTRY" (Bad R:R)
        # If SL and TP exist, check Risk:Reward
        if sl > 0 and tp > 0:
            risk = abs(entry - sl)
            reward = abs(tp - entry)
            if risk > 0 and (reward / risk) < 0.8:
                tech_sentiment = "IMPULSIVE / POOR R:R"
                tech_desc = f"Your planned Risk-to-Reward was less than 1:1 ({(reward/risk):.2f}). This is statistically unsustainable."

        # D. CHECK FOR "HOPING" (Moving Stop Loss)
        # If the loss is significantly larger than implied by the SL (slippage aside)
        if sl > 0 and pnl < 0:
            expected_loss_per_share = abs(entry - sl)
            actual_loss_per_share = abs(entry - exit_price)
            if actual_loss_per_share > (expected_loss_per_share * 1.1): # 10% tolerance
                tech_sentiment = "UNDISCIPLINED / SLIPPAGE"
                tech_desc = "Your loss exceeded your Stop Loss distance. Did you move your stop or suffer major slippage?"

    # 4. FINAL SYNTHESIS
    # Use text if available, otherwise use technical analysis, otherwise generic PnL
    if text_sentiment:
        sentiment, emo_desc = text_sentiment
        if tech_sentiment != "NEUTRAL":
            emo_desc += f" [Technical Note: {tech_desc}]"
    elif tech_sentiment != "NEUTRAL":
        sentiment = tech_sentiment
        emo_desc = tech_desc
    else:
        # Fallback to simple PnL logic
        if pnl <= -100:
            sentiment = "ANXIOUS"
            emo_desc = f"Significant financial loss of ${abs(pnl)} detected."
        elif pnl > 500:
            sentiment = "EUPHORIC"
            emo_desc = "High-profit outlier."
        elif pnl > 0:
            sentiment = "CALM"
            emo_desc = "Profitable outcome within standard parameters."

   # --- CLUSTER ENGINE (ML Pattern Recognition) ---
    strategy = "Analyzing..."
    strat_desc = ""

    try:
        from sklearn.cluster import KMeans, DBSCAN
        from sklearn.preprocessing import StandardScaler
        import numpy as np

        # Extract features for the current trade to cluster
        current_risk = abs(entry - sl) if sl > 0 else 0
        current_reward = abs(tp - entry) if tp > 0 else 0
        current_features = [pnl, entry, exit_price, current_risk, current_reward]

        # Fetch recent historical trades for this symbol to build the ML dataset
        # (Grabs the last 200 trades to find mathematical patterns)
        historical_trades = Trade.objects.filter(symbol=symbol).order_by('-close_time')[:200]
        
        if len(historical_trades) < 10:
            strategy = "Insufficient Data"
            strat_desc = "Need at least 10 historical trades on this asset to perform ML clustering."
        else:
            # Build feature matrix (X)
            X = []
            for t in historical_trades:
                t_entry = float(t.open_price or 0)
                t_exit = float(t.close_price or 0)
                t_sl = float(t.stop_loss or 0)
                t_tp = float(t.take_profit or 0)
                
                t_risk = abs(t_entry - t_sl) if t_sl > 0 else 0
                t_reward = abs(t_tp - t_entry) if t_tp > 0 else 0
                t_pnl = float(t.profit or 0)
                
                X.append([t_pnl, t_entry, t_exit, t_risk, t_reward])
            
            # Append current trade and normalize data
            X.append(current_features)
            X_array = np.array(X)
            scaler = StandardScaler()
            X_scaled = scaler.fit_transform(X_array)

            # 1. DBSCAN for Anomaly Detection (Outliers)
            dbscan = DBSCAN(eps=1.5, min_samples=3)
            dbscan_labels = dbscan.fit_predict(X_scaled)
            is_anomaly = dbscan_labels[-1] == -1

            if is_anomaly:
                strategy = "Anomaly / Outlier Setup"
                strat_desc = "DBSCAN identified this trade as mathematically unusual compared to your standard habits."
            else:
                # 2. K-Means for Pattern Grouping
                n_clusters = min(4, len(X) // 3) # Dynamic cluster sizing based on data volume
                kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init='auto')
                kmeans_labels = kmeans.fit_predict(X_scaled)
                current_cluster = kmeans_labels[-1]
                
                # Analyze the cluster center to generate an intelligent description
                center = scaler.inverse_transform([kmeans.cluster_centers_[current_cluster]])[0]
                avg_pnl, avg_entry, avg_exit, avg_risk, avg_reward = center
                
                strategy = f"Pattern Cluster Alpha-{current_cluster + 1}"
                
                if avg_pnl > 0 and avg_reward > (avg_risk * 1.5):
                    strat_desc = f"Winning Profile. ML groups this with your highest R:R winning trades (Avg Cluster PnL: ${avg_pnl:.2f})."
                elif avg_pnl < 0:
                    strat_desc = f"Losing Profile. Algorithms group this with your typical losing setups (Avg Cluster PnL: ${avg_pnl:.2f})."
                else:
                    strat_desc = f"Standard Execution. Fits your normal volume and volatility baseline (Avg Cluster PnL: ${avg_pnl:.2f})."

    except ImportError:
        strategy = "ML Modules Missing"
        strat_desc = "Please run: pip install scikit-learn numpy"
    except Exception as e:
        strategy = "Clustering Error"
        strat_desc = f"Engine failed to process patterns: {str(e)}"

    return {
        'emotion': { 'status': sentiment, 'description': emo_desc },
        'cluster': { 'strategy': strategy, 'description': strat_desc }
    }


# --- 2. UPDATED VIEW ---
from datetime import timedelta
from django.utils.dateparse import parse_datetime

@login_required
def trade_detail(request, trade_id):
    current_account, user_accounts = get_active_account(request)
    target_trade = None

    # --- SAFE FLOAT HELPER ---
    def _safe_float(val):
        try:
            if val is None or str(val).strip() in ['', 'None', 'null']: 
                return 0.0
            return float(val)
        except (ValueError, TypeError):
            return 0.0

    # [BLOCK A: SNAPTRADE LOGIC]
    if current_account and current_account.snaptrade_user_id:
        try:
            client = TradeSmartCloud()
            ST_UID = current_account.snaptrade_user_id
            ST_SECRET = current_account.user_secret
            
            cloud_accounts = client.get_accounts(ST_UID, ST_SECRET)
            if cloud_accounts:
                acc_id = cloud_accounts[0]['id']
                # DEEP FETCH: 365 Days
                orders_res = client.api.account_information.get_user_account_orders(ST_UID, ST_SECRET, acc_id, state='all', days=365)
                all_orders = orders_res.body if isinstance(orders_res.body, list) else []
                chron_orders = sorted(all_orders, key=lambda x: x.get('time_executed') or '0000')
                
                inventory = {} 

                for order in chron_orders:
                    sym_obj = order.get('universal_symbol') or order.get('symbol')
                    symbol = extract_clean_symbol(sym_obj)
                    action = order.get('action', '').upper()
                    units = float(order.get('filled_quantity') or 0.0)
                    price = float(order.get('execution_price') or 0.0)
                    
                    # Parse Execution Time
                    exec_time_str = order.get('time_executed')
                    current_order_time = parse_datetime(exec_time_str) if exec_time_str else timezone.now()

                    # Initialize Inventory with a 'last_close_time' tracker
                    if symbol not in inventory: 
                        inventory[symbol] = {
                            'qty': 0.0, 
                            'avg_price': 0.0,
                            # Default "start" time for the very first trade found
                            'last_close_time': current_order_time - timedelta(days=365) 
                        }
                    
                    curr = inventory[symbol]
                    trade_closed = False
                    trade_pnl = 0.0
                    opening_price = 0.0

                    # --- FIFO / PnL Logic ---
                    if action == 'BUY':
                        if curr['qty'] < 0: # Closing Short
                            opening_price = curr['avg_price']
                            qty_closed = min(abs(curr['qty']), units)
                            trade_pnl = (curr['avg_price'] - price) * qty_closed
                            trade_closed = True
                            curr['qty'] += qty_closed
                        else:
                            total = (curr['qty'] * curr['avg_price']) + (units * price)
                            curr['qty'] += units
                            if curr['qty'] > 0: curr['avg_price'] = total / curr['qty']
                    elif action == 'SELL':
                        if curr['qty'] > 0: # Closing Long
                            opening_price = curr['avg_price']
                            qty_closed = min(curr['qty'], units)
                            trade_pnl = (price - curr['avg_price']) * qty_closed
                            trade_closed = True
                            curr['qty'] -= qty_closed
                        else:
                            total = (abs(curr['qty']) * curr['avg_price']) + (units * price)
                            curr['qty'] -= units
                            if abs(curr['qty']) > 0: curr['avg_price'] = total / abs(curr['qty'])

                    # --- MATCHING LOGIC ---
                    if trade_closed:
                        current_id = generate_trade_id(order)
                        
                        # Only run this if we found the specific trade user requested
                        if current_id == trade_id:
                            # Safe Float Extraction for SL/TP
                            sl = _safe_float(order.get('stop_loss'))
                            tp = _safe_float(order.get('take_profit'))

                            target_trade = {
                                'id': trade_id,
                                'ticket': trade_id, 
                                'symbol': symbol,
                                'type': 'LONG' if action == 'SELL' else 'SHORT',
                                'profit': round(trade_pnl, 2),
                                'entry_price': round(opening_price, 2),
                                'open_price': round(opening_price, 2),
                                'exit_price': round(price, 2),
                                'lot_size': units,
                                'stop_loss': sl, 
                                'take_profit': tp, 
                                'open_time': current_order_time, # Approximation
                                'close_time': current_order_time
                            }
                            
                            # --- SMART NOTE MATCHING ---
                            # 1. Broker Notes: Exact ID match
                            broker_notes = TradeNote.objects.filter(trade__trade_id=trade_id)

                            # 2. Live Notes: "Window" Match
                            # We look for notes created AFTER the PREVIOUS trade closed, but BEFORE this one closed.
                            # This prevents Trade B from stealing Trade A's notes.
                            window_start = curr['last_close_time']
                            window_end = current_order_time + timedelta(minutes=10) # Small buffer for notes made right at close
                            
                            live_notes = TradeNote.objects.filter(
                                trade__user=request.user,
                                trade__symbol=symbol,
                                trade__trade_id__icontains='LIVE',
                                trade__open_time__gt=window_start, # Must be newer than previous trade
                                trade__open_time__lte=window_end   # Must be older than current close
                            )

                            # 3. Combine
                            target_trade['notes'] = (broker_notes | live_notes).distinct().order_by('-created_at')

                            # --- POST HANDLERS ---
                            if request.method == 'POST':
                                if 'delete_note_id' in request.POST:
                                    TradeNote.objects.filter(id=request.POST.get('delete_note_id')).delete()
                                    return redirect('trade_detail', trade_id=trade_id)
                                elif 'review_type' in request.POST:
                                    # Handle Checklist Review Form
                                    review_type = request.POST.get('review_type')
                                    if review_type == 'win_review':
                                        answer = request.POST.get('followed_rules')
                                        content = f"[CHECKLIST REVIEW - WIN]: Followed rules? {answer.upper()}"
                                    elif review_type == 'loss_review':
                                        answer = request.POST.get('loss_type')
                                        content = f"[CHECKLIST REVIEW - LOSS]: Type of loss? {answer.upper()}"
                                    
                                    db_trade_obj, _ = Trade.objects.get_or_create(
                                        trade_id=trade_id,
                                        defaults={'user': request.user, 'symbol': symbol, 'profit': trade_pnl, 'close_time': current_order_time}
                                    )
                                    TradeNote.objects.create(trade=db_trade_obj, content=content)
                                    messages.success(request, "Trade review saved.")
                                    return redirect('trade_detail', trade_id=trade_id)
                                elif 'note' in request.POST:
                                    content = request.POST.get('note')
                                    if content.strip():
                                        db_trade_obj, _ = Trade.objects.get_or_create(
                                            trade_id=trade_id,
                                            defaults={'user': request.user, 'symbol': symbol, 'profit': trade_pnl, 'close_time': current_order_time}
                                        )
                                        TradeNote.objects.create(trade=db_trade_obj, content=content)
                                        return redirect('trade_detail', trade_id=trade_id)
                            break
                        
                        # IMPORTANT: Update the "Last Close Time" for this symbol
                        # This moves the window forward so the next trade (if any) starts searching from here.
                        curr['last_close_time'] = current_order_time

        except Exception as e:
            print(f"Cloud Error: {e}")

    # [BLOCK B: LOCAL DB FALLBACK]
    if not target_trade:
        t = Trade.objects.filter(trade_id=trade_id).prefetch_related(
            Prefetch('notes', queryset=TradeNote.objects.order_by('-created_at'))
        ).first()
        if t:
            target_trade = {
                'id': t.trade_id, 'ticket': t.trade_id, 'symbol': t.symbol, 'type': t.direction,
                'profit': float(t.profit), 'entry_price': float(t.open_price), 'open_price': float(t.open_price),
                'exit_price': float(t.close_price), 'lot_size': float(t.lot_size),
                'stop_loss': _safe_float(t.stop_loss),
                'take_profit': _safe_float(t.take_profit),
                'open_time': t.open_time, 'close_time': t.close_time,
                'notes': t.notes.all()
            }
            if request.method == 'POST':
                if 'delete_note_id' in request.POST:
                    TradeNote.objects.filter(id=request.POST.get('delete_note_id')).delete()
                    return redirect('trade_detail', trade_id=trade_id)
                elif 'review_type' in request.POST:
                    # Handle Checklist Review Form for DB trades
                    review_type = request.POST.get('review_type')
                    if review_type == 'win_review':
                        answer = request.POST.get('followed_rules')
                        content = f"[CHECKLIST REVIEW - WIN]: Followed rules? {answer.upper()}"
                    elif review_type == 'loss_review':
                        answer = request.POST.get('loss_type')
                        content = f"[CHECKLIST REVIEW - LOSS]: Type of loss? {answer.upper()}"
                    
                    TradeNote.objects.create(trade=t, content=content)
                    messages.success(request, "Trade review saved.")
                    return redirect('trade_detail', trade_id=trade_id)
                elif 'note' in request.POST:
                    TradeNote.objects.create(trade=t, content=request.POST.get('note'))
                    return redirect('trade_detail', trade_id=trade_id)

    # [BLOCK C: AI ANALYSIS (NLP INTEGRATION)]
    if target_trade:
        latest_note_text = ""
        # NEW: Variables to track if a checklist review already exists
        checklist_review_type = None
        checklist_review_answer = None

        if target_trade.get('notes') and len(target_trade['notes']) > 0:
            # We want to analyze actual journal notes, not the automated checklist reviews
            for note in target_trade['notes']:
                # Extract checklist data if it exists
                if "[CHECKLIST REVIEW" in note.content:
                    if "- WIN]:" in note.content:
                        checklist_review_type = "win_review"
                        # Extract the answer ("YES" or "NO") from the end of the string
                        checklist_review_answer = note.content.split("?")[-1].strip().lower()
                    elif "- LOSS]:" in note.content:
                        checklist_review_type = "loss_review"
                        # Extract the answer ("INEVITABLE" or "PSYCHOLOGICAL") 
                        checklist_review_answer = note.content.split("?")[-1].strip().lower()
                else:
                    # If it's a regular note, keep it for the NLP engine
                    if not latest_note_text:
                        latest_note_text = note.content
        
        # Attach the checklist status to the target_trade dictionary
        target_trade['checklist_reviewed'] = True if checklist_review_type else False
        target_trade['checklist_answer'] = checklist_review_answer

        # 1. Get Base Analysis (Technical metrics + clustering)
        ai_data = generate_ai_analysis(target_trade, latest_note_text)
        
        # 2. VADER + SpaCy Sentiment Override
        if latest_note_text:
            try:
                from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
                import spacy
                
                # Analyze Sentiment Polarity
                analyzer = SentimentIntensityAnalyzer()
                vs = analyzer.polarity_scores(latest_note_text)
                compound = vs['compound']
                
                # Use SpaCy to extract context (Noun Chunks)
                context_str = ""
                try:
                    nlp = spacy.load("en_core_web_sm")
                    doc = nlp(latest_note_text)
                    # Filter out common generic trading words to find actual context
                    ignore_words = ['i', 'me', 'my', 'trade', 'price', 'market', 'chart']
                    keywords = [chunk.text for chunk in doc.noun_chunks if chunk.text.lower() not in ignore_words]
                    
                    if keywords:
                        # Grab the top 2 meaningful noun chunks for context
                        context_str = f" Context focus: {', '.join(keywords[:2])}."
                except Exception as spacy_err:
                    print(f"SpaCy Error: {spacy_err}")

                # Map Compound Score (-1 to 1) to Trading Emotions
                if compound <= -0.5:
                    ai_data['emotion']['status'] = "TILTED / ANXIOUS"
                    ai_data['emotion']['description'] = f"NLP detected high negative sentiment (VADER Score: {compound:.2f}).{context_str}"
                elif -0.5 < compound < 0.0:
                    ai_data['emotion']['status'] = "NERVOUS / HESITANT"
                    ai_data['emotion']['description'] = f"NLP detected mild negative sentiment (VADER Score: {compound:.2f}).{context_str}"
                elif 0.0 <= compound <= 0.5:
                    ai_data['emotion']['status'] = "CALM / DISCIPLINED"
                    ai_data['emotion']['description'] = f"NLP detected balanced/neutral sentiment (VADER Score: {compound:.2f}).{context_str}"
                else:
                    ai_data['emotion']['status'] = "EUPHORIC / GREEDY"
                    ai_data['emotion']['description'] = f"NLP detected extreme positive sentiment (VADER Score: {compound:.2f}). Beware of overconfidence.{context_str}"

            except ImportError:
                print("NLP modules missing. Run: pip install vaderSentiment spacy && python -m spacy download en_core_web_sm")
            except Exception as e:
                print(f"NLP Engine Error: {e}")

        target_trade['ai_emotion'] = ai_data['emotion']
        target_trade['ai_cluster'] = ai_data['cluster']
    else:
        target_trade = {'symbol': 'Not Found', 'profit': 0, 'ai_emotion':{}, 'ai_cluster':{}}

    return render(request, 'trade_detail.html', {
        'trade': target_trade, 'current_account': current_account, 'accounts': user_accounts
    })

@login_required
def add_account(request):
    if request.method == 'POST':
        action_type = request.POST.get('action_type')
        custom_name = request.POST.get('nickname', 'My Broker Account')
        
        # --- OPTION A: MANUAL IMPORT ---
        if action_type == 'manual':
            # 1. Store the intended name in the session (Temporary)
            request.session['pending_manual_name'] = custom_name
            
            # 2. Redirect to import page without creating an account yet
            return redirect('import_trades')

        # --- OPTION B: API ACCOUNT (SNAPTRADE) ---
        else:
            client = TradeSmartCloud()
            new_st_id = f"TradeSmart_User_{request.user.id}_{str(uuid.uuid4())[:8]}"
            
            # Register on SnapTrade side to get IDs
            user_id, user_secret = client.register_user(new_st_id)
            
            if user_id and user_secret:
                # 1. Store credentials in session (Temporary)
                request.session['pending_api_data'] = {
                    'broker_name': custom_name,
                    'user_id': user_id,
                    'user_secret': user_secret
                }
                
                # 2. Build Redirect URL
                base_url = request.build_absolute_uri(reverse('dashboard'))
                dashboard_url = f"{base_url}?connected=true"
                
                # 3. Generate Link
                login_link = client.generate_login_link(
                    user_id=user_id, 
                    user_secret=user_secret, 
                    redirect_uri=dashboard_url
                )
                
                return redirect(login_link)

    return render(request, 'add_account.html')
@login_required
def settings_view(request):
    # 1. Get User & Profile
    user = request.user
    profile, created = UserProfile.objects.get_or_create(user=user)

    # 2. Handle Form Submission
    if request.method == 'POST':
        # Update User Model Fields
        user.first_name = request.POST.get('first_name')
        user.last_name = request.POST.get('last_name')
        user.email = request.POST.get('email')
        user.save()

        # Update Profile Model Fields
        profile.risk_appetite = request.POST.get('risk_appetite')
        profile.save()

        messages.success(request, "Profile settings updated successfully.")
        return redirect('settings')

    # 3. Get Account Data for Context
    accounts = TradingAccount.objects.filter(user=user)
    current_account, _ = get_active_account(request)

    context = {
        'user': user,       # Pass user object to pre-fill form
        'profile': profile, # Pass profile for risk settings
        'accounts': accounts,
        'current_account': current_account,
    }
    return render(request, 'settings.html', context)

@login_required
@require_POST
def delete_user_account(request):
    # --- 0. INTERNET CONNECTIVITY CHECK ---
    # We verify connection before allowing a permanent destructive action
    try:
        # Attempt to connect to Google DNS (8.8.8.8) on port 53 to verify internet access
        socket.create_connection(("8.8.8.8", 53), timeout=3)
    except OSError:
        messages.error(request, "Action blocked: No internet connection detected. You must be online to delete your account.")
        return redirect('settings') # Redirect back to settings so they don't get stuck
    # ---------------------------------------

    user = request.user
    
    # Log out first to prevent session issues
    logout(request)
    
    # Delete the user (Cascades to all data)
    user.delete()
    
    messages.success(request, "Your account has been permanently deleted.")
    return redirect('login')

@login_required
def lot_calculator(request):
        current_account, _ = get_active_account(request)
        live_balance = 0.0

        if current_account:
            # Fetch balance from the Database (AccountBalance model)
            # We access it via the related_name='balance' defined in your models.py
            try:
                if hasattr(current_account, 'balance'):
                    live_balance = float(current_account.balance.amount)
            except Exception as e:
                print(f"Error fetching balance: {e}")
                live_balance = 0.0

        context = {
            'current_account': current_account,
            'live_balance': live_balance,  # <--- This passes the DB value to the HTML
        }
        return render(request, 'calculator.html', context)

@login_required
def import_trades(request):
    # 1. CHECK FOR PENDING MANUAL ACCOUNT CREATION
    if 'pending_manual_name' in request.session:
        current_account = None
    else:
        current_account, user_accounts = get_active_account(request)
        if not current_account:
            return redirect('add_account')

    if request.method == 'POST':
        form = TradeImportForm(request.POST, request.FILES)
        if form.is_valid():
            uploaded_file = request.FILES['file']
            
            # --- ACCOUNT SECURITY LOCK ---
            # Extract digits from filename (e.g., "105465177" or "70628401")
            filename = uploaded_file.name
            file_id_match = re.search(r'(\d{5,})', filename) 
            file_account_id = file_id_match.group(1) if file_id_match else None

            # If account is already locked, prevent mismatch
            if current_account and current_account.account_number and file_account_id:
                if current_account.account_number != file_account_id:
                    messages.error(request, f"Security Block: This file ID ({file_account_id}) does not match the locked account ID ({current_account.account_number}). Please switch accounts.")
                    return redirect('import_trades')

            try:
                # --- 0. LAZY ACCOUNT CREATION ---
                if not current_account and 'pending_manual_name' in request.session:
                    account_name = request.session.pop('pending_manual_name')
                    current_account = TradingAccount.objects.create(
                        user=request.user,
                        broker_name=account_name,
                        account_number=file_account_id 
                    )
                    request.session['active_account_id'] = current_account.id
                
                # Lock existing account if it wasn't locked yet
                elif current_account and not current_account.account_number and file_account_id:
                    current_account.account_number = file_account_id
                    current_account.save()

                if not current_account:
                    messages.error(request, "No account active for import.")
                    return redirect('add_account')

                # --- 1. LOAD DATA ROBUSTLY ---
                uploaded_file.seek(0)
                df_raw = None
                
                # Try Excel
                try: df_raw = pd.read_excel(uploaded_file, header=None, engine='openpyxl')
                except: pass
                
                # Try CSV variants
                if df_raw is None:
                    uploaded_file.seek(0)
                    try: df_raw = pd.read_csv(uploaded_file, header=None, encoding='utf-8-sig')
                    except: pass
                if df_raw is None:
                    uploaded_file.seek(0)
                    try: df_raw = pd.read_csv(uploaded_file, header=None) 
                    except: pass
                if df_raw is None:
                    uploaded_file.seek(0)
                    try: df_raw = pd.read_csv(uploaded_file, header=None, encoding='utf-16', sep='\t')
                    except: pass

                if df_raw is None:
                    if not Trade.objects.filter(account=current_account).exists():
                        current_account.delete()
                        request.session['pending_manual_name'] = current_account.broker_name
                    messages.error(request, "Could not read file. Check format.")
                    return redirect('import_trades')

                # =========================================================
                # PASS 1: EXTRACT BALANCE (Universal Search)
                # =========================================================
                for idx, row in df_raw.iterrows():
                    col0 = str(row.iloc[0]).lower().strip()
                    # Check first column for "Balance" keyword
                    # Also support Bybit "Wallet Balance" column later in Pass 2
                    if col0.startswith('balance') and 'drawdown' not in col0:
                        row_full_str = " ".join([str(x) for x in row.values])
                        clean_str = row_full_str.replace(',', '').replace(' ', '')
                        numbers = re.findall(r"[\d\.]+", clean_str)
                        valid_balances = []
                        for n in numbers:
                            try:
                                val = float(n)
                                if val > 0: valid_balances.append(val)
                            except: continue
                        if valid_balances:
                            final_balance = max(valid_balances)
                            AccountBalance.objects.update_or_create(
                                account=current_account,
                                defaults={'amount': final_balance}
                            )
                            break 

                # =========================================================
                # PASS 2: EXTRACT POSITIONS (UNIVERSAL HEADER SEARCH)
                # =========================================================
                header_row_idx = None
                
                # We need to find a row that contains keywords for Time, Symbol, and Money
                # Logic: (Time OR Date) AND (Symbol OR Contract) AND (Price OR Profit OR Change OR Amount)
                
                for idx, row in df_raw.iterrows():
                    # Limit scan to first 20 rows for efficiency
                    if idx > 20: break 
                    
                    row_str = " ".join([str(x).lower() for x in row.values])
                    
                    has_time = 'time' in row_str or 'date' in row_str or 'timestamp' in row_str
                    has_symbol = 'symbol' in row_str or 'contract' in row_str or 'coin' in row_str
                    has_money = 'profit' in row_str or 'price' in row_str or 'change' in row_str or 'amount' in row_str or 'cash flow' in row_str or 'pnl' in row_str
                    
                    if has_time and has_symbol and has_money:
                        header_row_idx = idx
                        break
                
                # Fallback: Check strictly first row if scan failed
                if header_row_idx is None:
                    row0_str = " ".join([str(x).lower() for x in df_raw.iloc[0].values])
                    if ('symbol' in row0_str or 'contract' in row0_str) and ('profit' in row0_str or 'change' in row0_str or 'pnl' in row0_str):
                        header_row_idx = 0

                if header_row_idx is None:
                    if not Trade.objects.filter(account=current_account).exists():
                        current_account.delete()
                        request.session['pending_manual_name'] = current_account.broker_name
                    messages.error(request, "Could not identify header row. Ensure file has 'Time', 'Symbol', and 'Price/Profit' columns.")
                    return redirect('import_trades')

                df_trades = df_raw.iloc[header_row_idx+1:].copy()
                
                # --- NUCLEAR HEADER CLEANING & MAPPING ---
                raw_cols_dirty = df_raw.iloc[header_row_idx].astype(str).tolist()
                # Clean: "S / L" -> "sl", "Time(UTC)" -> "timeutc", "Filled Price" -> "filledprice"
                normalized_cols = [re.sub(r'[^a-zA-Z0-9]', '', col).lower() for col in raw_cols_dirty]

                deduped_cols = []
                col_counts = {}
                for col in normalized_cols:
                    if col in col_counts:
                        col_counts[col] += 1
                        deduped_cols.append(f"{col}_{col_counts[col]}")
                    else:
                        col_counts[col] = 0
                        deduped_cols.append(col)
                df_trades.columns = deduped_cols
                
                # --- DYNAMIC COLUMN IDENTIFICATION ---
                # We map generic concepts (Symbol, Price, Profit) to the specific column names found in this file
                
                col_map = {
                    'symbol': None, 'time': None, 'time_close': None, 'type': None, 'lot': None,
                    'price_open': None, 'price_close': None, 'profit': None,
                    'sl': None, 'tp': None, 'fee': None
                }

                for col in df_trades.columns:
                    # Symbol
                    if not col_map['symbol'] and (col in ['symbol', 'contract', 'coin', 'pair']): 
                        col_map['symbol'] = col
                    
                    # Time (Open)
                    if not col_map['time'] and (col in ['time', 'opentime', 'datetime', 'timeutc', 'date', 'boughttimestamp', 'entrytime']):
                        col_map['time'] = col
                    
                    # Time (Close)
                    if not col_map['time_close'] and (col in ['closetime', 'time_1', 'timeclose', 'date_1', 'soldtimestamp', 'exittime']):
                        col_map['time_close'] = col
                    
                    # Type/Direction
                    if not col_map['type'] and (col in ['type', 'direction', 'side', 'action']):
                        col_map['type'] = col
                    
                    # Lots/Quantity
                    if not col_map['lot'] and (col in ['volume', 'size', 'quantity', 'qty', 'amount']):
                        col_map['lot'] = col
                        
                    # Prices (Open vs Close vs General) - FIXED TO IGNORE "FORMAT"
                    if 'openprice' in col or col == 'price' or col == 'filledprice' or col == 'buyprice' or col == 'entryprice':
                        if not col_map['price_open']: col_map['price_open'] = col
                    elif 'closeprice' in col or col == 'sellprice' or col == 'exitprice':
                        if not col_map['price_close']: col_map['price_close'] = col
                    elif 'price' in col and 'format' not in col and col != col_map['price_open']:
                        if not col_map['price_close']: col_map['price_close'] = col
                        
                    # Profit (PnL, Change, Cash Flow)
                    if not col_map['profit'] and (col in ['profit', 'change', 'netprofit', 'cashflow', 'amount', 'pnl']):
                        col_map['profit'] = col
                        
                    # Fees
                    if not col_map['fee'] and (col in ['commission', 'fee', 'swap', 'feepaid']):
                        col_map['fee'] = col

                    # SL / TP
                    if col == 'sl' or 'stoploss' in col: col_map['sl'] = col
                    if col == 'tp' or 'takeprofit' in col: col_map['tp'] = col

                # Fallback: If price_close is missing, use price_open (common in single-row execution logs)
                if not col_map['price_close']: col_map['price_close'] = col_map['price_open']

                def safe_float(val):
                    if val is None: return 0.0
                    s_val = str(val).replace(',', '').replace(' ', '').replace('$', '').strip()
                    if s_val in ['-', '', 'nan', 'none', 'null', '--']: return 0.0
                    
                    # Handle accounting parentheses for negative numbers (e.g. $(265.00) -> -265.0)
                    is_negative = False
                    if s_val.startswith('(') and s_val.endswith(')'):
                        is_negative = True
                        s_val = s_val[1:-1]

                    try: 
                        val_float = float(s_val)
                        return -val_float if is_negative else val_float
                    except: return 0.0

                parsed_trades = [] 
                
                # Helper to find ID column
                ticket_col = None
                possible_id_cols = ['ticket', 'order', 'deal', 'id', 'positionid', 'orderid', 'uid']
                for col in df_trades.columns:
                    if col in possible_id_cols: ticket_col = col; break

                # --- ROW PROCESSING LOOP ---
                for index, row in df_trades.iterrows():
                    # 1. Skip non-data rows
                    col0 = str(row.iloc[0]).lower().strip()
                    if any(x in col0 for x in ['order', 'deal', 'summary', 'total']) or col0.startswith('balance'): break
                    
                    # 2. Extract Data using Map
                    symbol = row.get(col_map['symbol'])
                    if pd.isna(symbol) or str(symbol).strip() == '': continue # Skip rows without symbol (e.g. transfers)

                    raw_type = str(row.get(col_map['type']) or '').lower().strip()
                    pnl = safe_float(row.get(col_map['profit']))
                    lots = safe_float(row.get(col_map['lot']))
                    entry = safe_float(row.get(col_map['price_open']))
                    exit_price = safe_float(row.get(col_map['price_close']))
                    
                    sl = safe_float(row.get(col_map['sl'])) if col_map['sl'] else 0.0
                    tp = safe_float(row.get(col_map['tp'])) if col_map['tp'] else 0.0
                    fee = safe_float(row.get(col_map['fee'])) if col_map['fee'] else 0.0

                    # Time Parsing (Open)
                    raw_time = row.get(col_map['time'])
                    try: 
                        open_time = pd.to_datetime(raw_time)
                    except: 
                        open_time = timezone.now()
                    
                    # Time Parsing (Close)
                    # If we found a second time column (e.g. time_1), use it. Otherwise default to open_time.
                    raw_time_close = row.get(col_map['time_close'])
                    if raw_time_close:
                        try:
                            close_time = pd.to_datetime(raw_time_close)
                        except:
                            close_time = open_time
                    else:
                        close_time = open_time

                    # Direction inference for formats with no explicit type but valid buy/sell timestamps
                    if raw_type not in ['buy', 'sell', 'long', 'short']: 
                        if 'transfer' in raw_type: continue
                        
                        if col_map['time'] == 'boughttimestamp' and col_map['time_close'] == 'soldtimestamp':
                            if open_time <= close_time:
                                raw_type = 'buy'
                            else:
                                raw_type = 'sell'
                                # It's a short! Swap times and prices to reflect reality.
                                open_time, close_time = close_time, open_time
                                entry, exit_price = exit_price, entry
                        else:
                            if not raw_type and col_map['symbol']: raw_type = 'buy' # Fallback

                    # --- NEW VALIDATION: SKIP TRADES WITHOUT ENTRY/EXIT PRICES ---
                    if entry <= 0 or exit_price <= 0: continue
                    # -----------------------------------------------------------
                    
                    # Robust ID Generation
                    unique_str = f"{current_account.id}-{open_time}-{symbol}-{pnl}-{lots}"
                    if ticket_col: unique_str += f"-{row[ticket_col]}"
                    else: unique_str += f"-row{index}"
                    unique_id = hashlib.md5(unique_str.encode()).hexdigest()

                    parsed_trades.append({
                        'trade_id': unique_id,
                        'data': {
                            'user': request.user,
                            'account': current_account,
                            'symbol': symbol,
                            'direction': 'BUY' if raw_type in ['buy', 'long'] else 'SELL',
                            'lot_size': lots,
                            'open_price': entry,
                            'close_price': exit_price,
                            'profit': pnl,
                            'commission': abs(fee), # Store fee separately if found
                            'stop_loss': sl,
                            'take_profit': tp,
                            'open_time': open_time,
                            'close_time': close_time,
                            'source_platform': 'Manual Import',
                            'status': 'CLOSED'
                        }
                    })

                if not parsed_trades:
                    if not Trade.objects.filter(account=current_account).exists():
                        current_account.delete()
                        request.session['pending_manual_name'] = current_account.broker_name
                    messages.warning(request, "No valid trades found. Checked 0 rows.")
                    return redirect('journal')

                # --- BATCH INSERT (Existing Logic) ---
                file_ids = [t['trade_id'] for t in parsed_trades]
                existing_ids_in_db = set(
                    Trade.objects.filter(account=current_account, trade_id__in=file_ids)
                    .values_list('trade_id', flat=True)
                )

                trades_to_create = []
                seen_ids_in_this_file = set() 

                for t in parsed_trades:
                    tid = t['trade_id']
                    if tid not in existing_ids_in_db and tid not in seen_ids_in_this_file:
                        trades_to_create.append(Trade(trade_id=tid, **t['data']))
                        seen_ids_in_this_file.add(tid)
                
                action_msg = f"Import successful. Added {len(trades_to_create)} new trades."

                if trades_to_create:
                    Trade.objects.bulk_create(trades_to_create)

                bal_obj = AccountBalance.objects.filter(account=current_account).first()
                bal_msg = f" Balance: ${bal_obj.amount:,.2f}." if bal_obj else ""
                
                messages.success(request, f"{action_msg}{bal_msg}")
                return redirect('journal')

            except Exception as e:
                if 'pending_manual_name' not in request.session and current_account and not Trade.objects.filter(account=current_account).exists():
                      account_name = current_account.broker_name
                      current_account.delete()
                      request.session['pending_manual_name'] = account_name

                messages.error(request, f"Error processing file: {e}")
                
    else:
        form = TradeImportForm()

    return render(request, 'import_trades.html', {'form': form, 'current_account': current_account})
def ai_predict_trade(request):
    """
    REAL AI ENGINE (5-Minute Intraday Scalper Mode)
    """
    if request.method == 'GET':
        symbol = request.GET.get('symbol', 'SPY').upper()
        user_side = request.GET.get('side', 'LONG').upper() 
        
        # 1. NORMALIZE SYMBOL
        ticker_symbol = symbol
        if "USD" in symbol and len(symbol) == 6: ticker_symbol = f"{symbol}=X"
        elif symbol in ['BTC', 'ETH', 'SOL', 'XRP', 'DOGE']: ticker_symbol = f"{symbol}-USD"

        try:
            # 2. FETCH INTRADAY DATA (5-Minute Candles)
            # We get the last 5 days to ensure we have enough data for indicators
            ticker = yf.Ticker(ticker_symbol)
            df = ticker.history(period="5d", interval="5m")
            
            if df.empty:
                return JsonResponse({'prediction': "UNKNOWN", 'confidence': 0, 'reason': "No intraday data."})

            # 3. CALCULATE INTRADAY INDICATORS
            # SMA 50 on 5m chart = The trend over the last ~4 hours
            df['SMA_50'] = df['Close'].rolling(window=50).mean()
            
            # RSI (14 periods * 5m = Momentum over last 70 mins)
            delta = df['Close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            df['RSI'] = 100 - (100 / (1 + rs))

            # Get Latest Live Candle
            current_price = df['Close'].iloc[-1]
            sma_50 = df['SMA_50'].iloc[-1]
            rsi = df['RSI'].iloc[-1]
            
            # 4. DETERMINE 5-MINUTE TREND
            market_bias = "NEUTRAL"
            market_score = 0
            
            # Intraday Trend Check
            if current_price > sma_50:
                market_score += 1
                trend_desc = "5m Uptrend"
            else:
                market_score -= 1
                trend_desc = "5m Downtrend"

            # Intraday Momentum Check
            if rsi < 30: market_score += 1 # Oversold bounce likely
            elif rsi > 70: market_score -= 1 # Overbought drop likely
            
            if market_score > 0: market_bias = "BULLISH"
            elif market_score < 0: market_bias = "BEARISH"

            # 5. CONTEXT AWARENESS (Compare 5m Trend vs. Position)
            prediction = "HOLD"
            confidence = 50
            
            # Generate "Trader Talk" Reason
            reason = f"5m Chart is {market_bias} ({trend_desc}). "

            # SCENARIO A: Good Scalp
            if (market_bias == "BULLISH" and user_side == "LONG") or \
               (market_bias == "BEARISH" and user_side == "SHORT"):
                prediction = "GOOD"
                confidence = 88
                reason += "Momentum aligns with your trade."
            
            # SCENARIO B: Bad Scalp
            elif (market_bias == "BULLISH" and user_side == "SHORT") or \
                 (market_bias == "BEARISH" and user_side == "LONG"):
                prediction = "WARNING"
                confidence = 25
                reason += f"Intraday reversal against you! Watch out."

            # SCENARIO C: Choppy Market
            else:
                prediction = "NEUTRAL"
                confidence = 50
                reason += "Price is chopping around SMA. No clear direction."

            return JsonResponse({
                'symbol': symbol,
                'prediction': prediction,
                'confidence': confidence,
                'reason': reason
            })

        except Exception as e:
            return JsonResponse({'prediction': "ERROR", 'reason': str(e)})

    return JsonResponse({'error': 'Invalid request'}, status=400)

def send_verification_code(request):
    if request.method == 'POST':
        email = request.POST.get('email')
        
        # 1. Generate 6-digit code
        code = str(random.randint(100000, 999999))
        
        # 2. Store in session with expiry (current time + 300 seconds for 5 mins)
        # I strongly recommend 5 minutes (300s) instead of 2 (120s) for email delays
        request.session['email_code'] = code
        request.session['email_verified'] = False
        request.session['auth_email'] = email
        request.session['code_expiry'] = time.time() + 300 

        # 3. Send Email
        try:
            send_mail(
                'Your TradeSmart Verification Code',
                f'Your verification code is: {code}. It expires in 5 minutes.',
                settings.EMAIL_HOST_USER,
                [email],
                fail_silently=False,
            )
            return JsonResponse({'status': 'success'})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)})

    return JsonResponse({'status': 'error', 'message': 'Invalid request'})

def verify_code(request):
    if request.method == 'POST':
        user_code = request.POST.get('code')
        session_code = request.session.get('email_code')
        expiry = request.session.get('code_expiry', 0)

        # 1. Check if code exists and matches
        if not session_code or user_code != session_code:
            return JsonResponse({'status': 'error', 'message': 'Invalid code'})

        # 2. Check Expiry
        if time.time() > expiry:
            return JsonResponse({'status': 'error', 'message': 'Code expired'})

        # 3. Success
        request.session['email_verified'] = True
        return JsonResponse({'status': 'success'})
        
    return JsonResponse({'status': 'error', 'message': 'Invalid request'})

# Add these along with your other views
def send_reset_code(request):
    if request.method == 'POST':
        email = request.POST.get('email')
        # Check if user exists first!
        if not User.objects.filter(email=email).exists():
            # Security: Don't reveal user doesn't exist, but don't send email
            # Or return error if you prefer convenience over strict security
            return JsonResponse({'status': 'error', 'message': 'Email not found.'})

        code = str(random.randint(100000, 999999))
        request.session['reset_code'] = code
        request.session['reset_email'] = email
        request.session['reset_verified'] = False
        request.session['reset_expiry'] = time.time() + 300 # 5 mins

        send_mail(
            'TradeSmart Password Reset',
            f'Your password reset code is: {code}',
            settings.EMAIL_HOST_USER,
            [email],
            fail_silently=False,
        )
        return JsonResponse({'status': 'success'})
    return JsonResponse({'status': 'error'})

def verify_reset_code(request):
    if request.method == 'POST':
        user_code = request.POST.get('code')
        session_code = request.session.get('reset_code')
        expiry = request.session.get('reset_expiry', 0)

        if not session_code or user_code != session_code:
            return JsonResponse({'status': 'error', 'message': 'Invalid code'})
        
        if time.time() > expiry:
            return JsonResponse({'status': 'error', 'message': 'Code expired'})

        request.session['reset_verified'] = True
        return JsonResponse({'status': 'success'})
    return JsonResponse({'status': 'error'})

def complete_password_reset(request):
    if request.method == 'POST':
        if not request.session.get('reset_verified'):
            return JsonResponse({'status': 'error', 'message': 'Unauthorized'})
        
        email = request.session.get('reset_email')
        new_pass = request.POST.get('password')
        
        try:
            user = User.objects.get(email=email)
            user.set_password(new_pass)
            user.save()
            
            # Clear session data
            del request.session['reset_code']
            del request.session['reset_email']
            del request.session['reset_verified']
            
            return JsonResponse({'status': 'success'})
        except User.DoesNotExist:
            return JsonResponse({'status': 'error', 'message': 'User error'})
            
    return JsonResponse({'status': 'error'})

@login_required
def reports_dashboard(request):
    current_account, user_accounts = get_active_account(request)
    if not current_account:
        return redirect('add_account')

    report_data = {} 

    # =========================================================
    # 1. DATA SOURCE: SNAPTRADE API (Live FIFO Calculation)
    # =========================================================
    if current_account and current_account.snaptrade_user_id:
        try:
            client = TradeSmartCloud()
            ST_UID = current_account.snaptrade_user_id
            ST_SECRET = current_account.user_secret
            
            cloud_accounts = client.get_accounts(ST_UID, ST_SECRET)
            if cloud_accounts:
                acc_id = cloud_accounts[0]['id']
                # DEEP FETCH: 365 Days
                orders_res = client.api.account_information.get_user_account_orders(ST_UID, ST_SECRET, acc_id, state='all', days=365)
                all_orders = orders_res.body if isinstance(orders_res.body, list) else []

                # Sort for FIFO
                chron_orders = sorted(all_orders, key=lambda x: x.get('time_executed') or x.get('time_placed') or '0000-00-00', reverse=False)
                inventory = {} 

                for order in chron_orders:
                    # [DATE PARSING]
                    raw_date = (order.get('time_executed') or order.get('time_placed'))
                    trade_dt = timezone.now()
                    if raw_date:
                        try:
                            if str(raw_date).isdigit(): 
                                trade_dt = make_aware(datetime.fromtimestamp(int(raw_date) / 1000.0))
                            else: 
                                trade_dt = datetime.fromisoformat(str(raw_date).replace('Z', '+00:00'))
                        except: pass

                    # [DATA EXTRACTION]
                    sym_obj = order.get('universal_symbol') or order.get('symbol')
                    symbol = extract_clean_symbol(sym_obj)
                    action = order.get('action', '').upper()
                    units = float(order.get('filled_quantity') or order.get('total_quantity') or 0.0)
                    price = float(order.get('execution_price') or order.get('price') or 0.0)
                    
                    trade_pnl = 0.0; trade_closed = False

                    # [FIFO LOGIC]
                    if symbol not in inventory: inventory[symbol] = {'qty': 0.0, 'avg_price': 0.0}
                    current_qty = inventory[symbol]['qty']; avg_entry = inventory[symbol]['avg_price']

                    if action == 'BUY':
                        if current_qty < 0: # Closing Short
                            cover_qty = min(abs(current_qty), units)
                            trade_pnl = (avg_entry - price) * cover_qty; trade_closed = True
                            inventory[symbol]['qty'] += cover_qty
                            if inventory[symbol]['qty'] == 0: inventory[symbol]['avg_price'] = 0.0
                            if units - cover_qty > 0: inventory[symbol]['qty'] += (units - cover_qty); inventory[symbol]['avg_price'] = price 
                        else: # Opening Long
                            total_val = (current_qty * avg_entry) + (units * price); new_total_qty = current_qty + units
                            if new_total_qty > 0: inventory[symbol]['avg_price'] = total_val / new_total_qty
                            inventory[symbol]['qty'] = new_total_qty
                    elif action == 'SELL':
                        if current_qty > 0: # Closing Long
                            close_qty = min(current_qty, units)
                            trade_pnl = (price - avg_entry) * close_qty; trade_closed = True
                            inventory[symbol]['qty'] -= close_qty
                            if inventory[symbol]['qty'] == 0: inventory[symbol]['avg_price'] = 0.0
                            if units - close_qty > 0: inventory[symbol]['qty'] -= (units - close_qty); inventory[symbol]['avg_price'] = price 
                        else: # Opening Short
                            total_val = (abs(current_qty) * avg_entry) + (units * price); new_total_abs_qty = abs(current_qty) + units
                            if new_total_abs_qty > 0: inventory[symbol]['avg_price'] = total_val / new_total_abs_qty
                            inventory[symbol]['qty'] -= units

                    # [AGGREGATE FOR DASHBOARD]
                    if trade_closed:
                        # Group by Month-Year (YYYY-MM)
                        key = trade_dt.strftime("%Y-%m")
                        
                        if key not in report_data:
                            report_data[key] = {
                                'year': trade_dt.year,
                                'month_num': trade_dt.month,
                                'month_name': trade_dt.strftime("%B"),
                                'net_pnl': 0.0,
                                'total_trades': 0,
                                'wins': 0
                            }
                        
                        data = report_data[key]
                        data['net_pnl'] += trade_pnl
                        data['total_trades'] += 1
                        if trade_pnl > 0:
                            data['wins'] += 1

        except Exception as e:
            print(f"Reports Dashboard API Error: {e}")

    # =========================================================
    # 2. DATA SOURCE: DATABASE (Manual Accounts)
    # =========================================================
    else:
        trades = Trade.objects.filter(account=current_account).order_by('-close_time')
        for t in trades:
            if not t.close_time: continue
            
            key = t.close_time.strftime("%Y-%m")
            
            if key not in report_data:
                report_data[key] = {
                    'year': t.close_time.year,
                    'month_num': t.close_time.month,
                    'month_name': t.close_time.strftime("%B"),
                    'net_pnl': 0.0,
                    'total_trades': 0,
                    'wins': 0
                }
            
            # Safe Float Conversion
            profit = float(t.profit) if t.profit is not None else 0.0
            
            data = report_data[key]
            data['net_pnl'] += profit
            data['total_trades'] += 1
            if profit > 0:
                data['wins'] += 1

    # --- FILTER & FORMAT FOR TEMPLATE ---
    today = datetime.now().date()
    reports = []
    
    # Sort keys to ensure reports are in order (Newest first)
    sorted_keys = sorted(report_data.keys(), reverse=True)

    for key in sorted_keys:
        data = report_data[key]
        
        # LOGIC: Only show if the month has fully passed
        # 1. Past Year OR 2. Current Year AND Past Month
        if data['year'] < today.year or (data['year'] == today.year and data['month_num'] < today.month):
            
            # Calculate stats
            wr = 0
            if data['total_trades'] > 0:
                wr = int((data['wins'] / data['total_trades']) * 100)
            data['win_rate'] = wr
            
            reports.append(data)

    return render(request, 'reports.html', {
        'reports': reports,
        'current_account': current_account
    })


class TradeAdapter:
    def __init__(self, **entries):
        self.__dict__.update(entries)

@login_required
def get_report_story(request, year, month):
    current_account, _ = get_active_account(request)
    trade_list = []

    # --- SAFE FLOAT HELPER ---
    def _safe_float(val):
        try:
            if val is None or str(val).strip() in ['', 'None', 'null']: 
                return 0.0
            return float(val)
        except (ValueError, TypeError):
            return 0.0

    # =========================================================
    # 1. DATA SOURCE: SNAPTRADE API (Live FIFO Calculation)
    # =========================================================
    if current_account and current_account.snaptrade_user_id:
        try:
            client = TradeSmartCloud()
            ST_UID = current_account.snaptrade_user_id
            ST_SECRET = current_account.user_secret
            
            cloud_accounts = client.get_accounts(ST_UID, ST_SECRET)
            if cloud_accounts:
                acc_id = cloud_accounts[0]['id']
                # DEEP FETCH: 365 Days
                orders_res = client.api.account_information.get_user_account_orders(ST_UID, ST_SECRET, acc_id, state='all', days=365)
                all_orders = orders_res.body if isinstance(orders_res.body, list) else []

                # Sort for FIFO
                chron_orders = sorted(all_orders, key=lambda x: x.get('time_executed') or x.get('time_placed') or '0000-00-00', reverse=False)
                inventory = {} 

                for order in chron_orders:
                    # [DATE PARSING]
                    raw_date = (order.get('time_executed') or order.get('time_placed') or order.get('time_updated') or order.get('execution_time'))
                    trade_dt = timezone.now()
                    
                    if raw_date:
                        try:
                            if str(raw_date).isdigit(): 
                                dt = datetime.fromtimestamp(int(raw_date) / 1000.0)
                                trade_dt = make_aware(dt)
                            else: 
                                trade_dt = datetime.fromisoformat(str(raw_date).replace('Z', '+00:00'))
                        except: pass

                    # [DATA EXTRACTION]
                    sym_obj = order.get('universal_symbol') or order.get('symbol')
                    symbol = extract_clean_symbol(sym_obj)
                    action = order.get('action', '').upper()
                    units = float(order.get('filled_quantity') or order.get('total_quantity') or 0.0)
                    price = float(order.get('execution_price') or order.get('price') or 0.0)
                    
                    # Commission/Swap extraction (Safe extraction)
                    comm = _safe_float(order.get('commission'))
                    swap = _safe_float(order.get('swap'))
                    
                    trade_pnl = 0.0; trade_closed = False; closing_price = price; opening_price = 0.0

                    # [FIFO LOGIC]
                    if symbol not in inventory: inventory[symbol] = {'qty': 0.0, 'avg_price': 0.0}
                    current_qty = inventory[symbol]['qty']; avg_entry = inventory[symbol]['avg_price']

                    if action == 'BUY':
                        if current_qty < 0: # Closing Short
                            opening_price = avg_entry; cover_qty = min(abs(current_qty), units)
                            trade_pnl = (avg_entry - price) * cover_qty; trade_closed = True
                            inventory[symbol]['qty'] += cover_qty
                            if inventory[symbol]['qty'] == 0: inventory[symbol]['avg_price'] = 0.0
                            if units - cover_qty > 0: inventory[symbol]['qty'] += (units - cover_qty); inventory[symbol]['avg_price'] = price 
                        else: # Opening Long
                            total_val = (current_qty * avg_entry) + (units * price); new_total_qty = current_qty + units
                            if new_total_qty > 0: inventory[symbol]['avg_price'] = total_val / new_total_qty
                            inventory[symbol]['qty'] = new_total_qty
                    elif action == 'SELL':
                        if current_qty > 0: # Closing Long
                            opening_price = avg_entry; close_qty = min(current_qty, units)
                            trade_pnl = (price - avg_entry) * close_qty; trade_closed = True
                            inventory[symbol]['qty'] -= close_qty
                            if inventory[symbol]['qty'] == 0: inventory[symbol]['avg_price'] = 0.0
                            if units - close_qty > 0: inventory[symbol]['qty'] -= (units - close_qty); inventory[symbol]['avg_price'] = price 
                        else: # Opening Short
                            total_val = (abs(current_qty) * avg_entry) + (units * price); new_total_abs_qty = abs(current_qty) + units
                            if new_total_abs_qty > 0: inventory[symbol]['avg_price'] = total_val / new_total_abs_qty
                            inventory[symbol]['qty'] -= units

                    # [FILTER FOR REPORT]
                    if trade_closed:
                        # Check if this specific trade belongs to the requested Month/Year
                        if trade_dt.year == int(year) and trade_dt.month == int(month):
                            
                            # Convert to Adapter Object so logic below works (t.profit, t.symbol)
                            trade_obj = TradeAdapter(
                                symbol=symbol,
                                profit=trade_pnl,
                                open_time=trade_dt, # Using execution time as both for simplicity in sorting
                                close_time=trade_dt,
                                direction='LONG' if action == 'SELL' else 'SHORT',
                                open_price=opening_price,
                                close_price=price,
                                lot_size=units,
                                # Manually attach commission/swap to adapter if supported, else 0
                                commission=comm,
                                swap=swap
                            )
                            # Monkey patch extra attributes if Adapter class doesn't support them by default
                            trade_obj.commission = comm
                            trade_obj.swap = swap
                            # Fetch existing notes from DB for this API trade to check checklist status
                            trade_obj.notes = list(TradeNote.objects.filter(trade__trade_id=generate_trade_id(order)))
                            trade_list.append(trade_obj)

        except Exception as e:
            print(f"Report API Error: {e}")
            return JsonResponse({'error': 'API Error'}, status=500)

    # =========================================================
    # 2. DATA SOURCE: DATABASE (Manual Accounts)
    # =========================================================
    else:
        db_trades = Trade.objects.filter(
            account=current_account,
            close_time__year=year,
            close_time__month=month
        ).prefetch_related('notes').order_by('close_time')
        trade_list = list(db_trades)

    # --- Sort for Analytics ---
    # Sort by time for Streak Analysis
    trade_list.sort(key=lambda x: x.close_time if x.close_time else x.open_time)

    if not trade_list:
        return JsonResponse({'error': 'No data'}, status=404)

    # --- 3. ANALYTICS CALCULATION ---
    count = len(trade_list)
    
    # Helper to safe cast
    def safe_pnl(t):
        val = getattr(t, 'profit', 0.0)
        return float(val) if val is not None else 0.0
    
    def safe_val(t, attr):
        val = getattr(t, attr, 0.0)
        return float(val) if val is not None else 0.0

    total_pnl = sum(safe_pnl(t) for t in trade_list)
    win_trades = [t for t in trade_list if safe_pnl(t) > 0]
    loss_trades = [t for t in trade_list if safe_pnl(t) <= 0]
    
    gross_win = sum(safe_pnl(t) for t in win_trades)
    gross_loss = abs(sum(safe_pnl(t) for t in loss_trades))
    
    # Basic Stats
    win_rate = int((len(win_trades) / count) * 100) if count > 0 else 0
    profit_factor = round(gross_win / gross_loss, 2) if gross_loss > 0 else 99.9
    avg_win = gross_win / len(win_trades) if win_trades else 0
    avg_loss = gross_loss / len(loss_trades) if loss_trades else 0
    rr_ratio = round(avg_win / avg_loss, 2) if avg_loss > 0 else 0

    best_trade = max(trade_list, key=safe_pnl) if trade_list else None
    worst_trade = min(trade_list, key=safe_pnl) if trade_list else None

    # New Extended Metrics
    total_lots = sum(safe_val(t, 'lot_size') for t in trade_list)
    total_fees = sum(safe_val(t, 'commission') + safe_val(t, 'swap') for t in trade_list)
    
    # Expectancy = (Win Rate * Avg Win) - (Loss Rate * Avg Loss)
    loss_rate_dec = (len(loss_trades) / count) if count > 0 else 0
    win_rate_dec = (len(win_trades) / count) if count > 0 else 0
    expectancy = (win_rate_dec * avg_win) - (loss_rate_dec * avg_loss)

    # Half Month Splits
    pnl_h1 = sum(safe_pnl(t) for t in trade_list if t.close_time and t.close_time.day <= 15)
    pnl_h2 = sum(safe_pnl(t) for t in trade_list if t.close_time and t.close_time.day > 15)

    # Streak Analysis
    current_win_streak = 0; max_win_streak = 0
    current_loss_streak = 0; max_loss_streak = 0
    
    for t in trade_list:
        pnl = safe_pnl(t)
        if pnl > 0:
            current_win_streak += 1; current_loss_streak = 0
            max_win_streak = max(max_win_streak, current_win_streak)
        else:
            current_loss_streak += 1; current_win_streak = 0
            max_loss_streak = max(max_loss_streak, current_loss_streak)

    # Time Analysis
    days_pnl = {}; hours_pnl = {}
    for t in trade_list:
        pnl = safe_pnl(t)
        if t.close_time:
            day_name = t.close_time.strftime("%A")
            days_pnl[day_name] = days_pnl.get(day_name, 0) + pnl
        if t.open_time:
            # Handle both Model object (datetime) and Adapter (datetime)
            if hasattr(t.open_time, 'hour'):
                h = t.open_time.hour
                hours_pnl[h] = hours_pnl.get(h, 0) + pnl
    
    best_day = max(days_pnl, key=days_pnl.get) if days_pnl else "N/A"
    worst_day = min(days_pnl, key=days_pnl.get) if days_pnl else "N/A"
    best_hour = max(hours_pnl, key=hours_pnl.get) if hours_pnl else 9
    best_hour_fmt = f"{best_hour}:00 - {best_hour+1}:00"

    # Symbol Analysis
    symbol_pnl = {}; symbol_count = {}
    for t in trade_list:
        sym = getattr(t, 'symbol', 'Unknown')
        if not sym: sym = "Unknown"
        symbol_pnl[sym] = symbol_pnl.get(sym, 0.0) + safe_pnl(t)
        symbol_count[sym] = symbol_count.get(sym, 0) + 1
        
    sorted_symbols = sorted(symbol_pnl.items(), key=lambda x: x[1], reverse=True)
    best_symbol_name = sorted_symbols[0][0] if sorted_symbols else "N/A"
    best_symbol_val = sorted_symbols[0][1] if sorted_symbols else 0
    worst_symbol_name = sorted_symbols[-1][0] if sorted_symbols else "N/A"
    worst_symbol_val = sorted_symbols[-1][1] if sorted_symbols else 0
    most_traded_sym = max(symbol_count, key=symbol_count.get) if symbol_count else "N/A"

    # Durations
    durations = []
    for t in trade_list:
        if t.open_time and t.close_time:
            durations.append((t.close_time - t.open_time).total_seconds())
    
    shortest_trade_sec = min(durations) if durations else 0
    longest_trade_sec = max(durations) if durations else 0
    avg_duration_sec = sum(durations) / len(durations) if durations else 0

    # --- AI AGGREGATION (Emotions & Strategies & Checklist) ---
    emotions_tally = {}
    strategies_tally = {}
    followed_rules_pnl = 0.0
    broke_rules_pnl = 0.0

    for t in trade_list:
        pnl = safe_pnl(t)
        t_dict = {
            'symbol': getattr(t, 'symbol', 'UNKNOWN'),
            'profit': pnl,
            'type': getattr(t, 'direction', 'LONG'),
            'entry_price': safe_val(t, 'open_price'),
            'exit_price': safe_val(t, 'close_price'),
            'stop_loss': safe_val(t, 'stop_loss'),
            'take_profit': safe_val(t, 'take_profit')
        }
        
        note_text = ""
        trade_notes = []
        try:
            if hasattr(t, 'notes'):
                if hasattr(t.notes, 'all'):
                    trade_notes = t.notes.all()
                else:
                    trade_notes = t.notes
                
                if trade_notes:
                    # Check for checklist outcomes first
                    for n in trade_notes:
                        content = n.content.upper()
                        if "[CHECKLIST REVIEW" in content:
                            if "YES" in content or "INEVITABLE" in content:
                                followed_rules_pnl += pnl
                                break
                            elif "NO" in content or "PSYCHOLOGICAL" in content:
                                broke_rules_pnl += pnl
                                break
                    
                    # Get the first actual journal note for NLP
                    for n in trade_notes:
                        if "[CHECKLIST REVIEW" not in n.content:
                            note_text = n.content
                            break
        except:
            pass
            
        try:
            # Assumes generate_ai_analysis is available in the module scope
            ai_data = generate_ai_analysis(t_dict, note_text)
            emo = ai_data['emotion']['status']
            strat = ai_data['cluster']['strategy']
            emotions_tally[emo] = emotions_tally.get(emo, 0) + 1
            strategies_tally[strat] = strategies_tally.get(strat, 0) + 1
        except:
            pass

    dominant_emotion = max(emotions_tally, key=emotions_tally.get) if emotions_tally else "NEUTRAL"
    dominant_strategy = max(strategies_tally, key=strategies_tally.get) if strategies_tally else "General Discretionary"

    # =========================================================
    # MODULE D: PATTERN FINDER (PCA + APRIORI)
    # =========================================================
    hidden_pattern_insight = "Need more trades to find complex patterns."
    pca_insight = ""

    if len(trade_list) >= 20: # Need enough data for association rules
        try:
            import pandas as pd
            import numpy as np
            from sklearn.decomposition import PCA
            from sklearn.preprocessing import StandardScaler
            from mlxtend.frequent_patterns import apriori, association_rules

            # --- 1. Data Preparation for ML ---
            ml_data = []
            for t in trade_list:
                # Skip invalid rows
                if not getattr(t, 'open_time', None): continue
                
                pnl = safe_pnl(t)
                is_win = pnl > 0
                
                # We categorize continuous variables into discrete "baskets" for Apriori
                hour = t.open_time.hour
                if hour < 6: time_of_day = "Asian Session"
                elif hour < 12: time_of_day = "London Session"
                elif hour < 17: time_of_day = "NY Session"
                else: time_of_day = "Late NY / Close"

                ml_data.append({
                    'Symbol': getattr(t, 'symbol', 'UNKNOWN'),
                    'Direction': getattr(t, 'direction', getattr(t, 'type', 'LONG')),
                    'TimeOfDay': time_of_day,
                    'DayOfWeek': t.open_time.strftime("%A"),
                    'Result': 'WIN' if is_win else 'LOSS',
                    # Numeric values kept for PCA
                    'Raw_Lot': safe_val(t, 'lot_size'),
                    'Raw_Duration': (t.close_time - t.open_time).total_seconds() if getattr(t, 'close_time', None) else 0,
                    'Raw_PnL': pnl
                })
            
            df = pd.DataFrame(ml_data)

            # --- 2. Principal Component Analysis (PCA) ---
            # Finding what numerical factors actually drive your PnL
            features = ['Raw_Lot', 'Raw_Duration']
            X_num = df[features].fillna(0)
            
            scaler = StandardScaler()
            X_scaled = scaler.fit_transform(X_num)
            
            pca = PCA(n_components=1)
            pca.fit(X_scaled)
            
            # Look at the principal component vector to see what impacts variance the most
            components = pca.components_[0]
            if abs(components[0]) > abs(components[1]) * 1.5:
                pca_insight = "PCA indicates your Position Size (Lot) is the primary driver of your PnL variance, far overriding hold times."
            elif abs(components[1]) > abs(components[0]) * 1.5:
                pca_insight = "PCA indicates your Hold Duration dictates your PnL outcomes more than your sizing."
            else:
                pca_insight = "PCA shows balanced variance between your sizing and duration."

            # --- 3. Apriori (Association Rules) ---
            # Prepare data: Apriori needs one-hot encoded binary columns
            apriori_df = df[['Symbol', 'Direction', 'TimeOfDay', 'DayOfWeek', 'Result']]
            encoded_df = pd.get_dummies(apriori_df)
            
            # Find frequent itemsets (combinations that happen at least 5% of the time)
            frequent_itemsets = apriori(encoded_df.astype(bool), min_support=0.05, use_colnames=True)
            
            if not frequent_itemsets.empty:
                # Generate rules
                rules = association_rules(frequent_itemsets, metric="confidence", min_threshold=0.6)
                
                # Filter for rules where the "consequent" (the outcome) is a WIN
                win_rules = rules[rules['consequents'] == frozenset({'Result_WIN'})]
                
                if not win_rules.empty:
                    # Sort by Lift (strength of the rule over random chance) and Confidence
                    best_rules = win_rules.sort_values(by=['lift', 'confidence'], ascending=[False, False])
                    top_rule = best_rules.iloc[0]
                    
                    # Clean up the output string
                    antecedents = [list(x)[0].split('_')[1] for x in top_rule['antecedents']]
                    conditions_str = " + ".join(antecedents)
                    confidence_pct = top_rule['confidence'] * 100
                    
                    hidden_pattern_insight = f"When you trade [ {conditions_str} ], you win {confidence_pct:.0f}% of the time."

        except ImportError:
            hidden_pattern_insight = "Machine learning libraries (mlxtend, sklearn) are missing."
        except Exception as e:
            hidden_pattern_insight = f"Pattern calculation error: {str(e)[:50]}"

    # --- BUILD SLIDES ---
    slides = []
    month_name = datetime(year, month, 1).strftime("%B")

    # 1. Intro
    slides.append({'type': 'intro', 'title': f"{month_name} {year}", 'value': "The Recap", 'subtitle': "Buckle up. We analyzed every single trade.", 'title_style': 'color: #888; letter-spacing: 2px;'})

    # 2. Net PnL
    pnl_color = '#00FF94' if total_pnl >= 0 else '#FF4D4D'
    slides.append({'type': 'profit' if total_pnl >= 0 else 'loss', 'title': "Net Result", 'value': f"${total_pnl:,.2f}", 'subtitle': f"Total P&L from {count} trades.", 'value_style': f"color: {pnl_color}; font-size: 60px;"})

    # --- NEW CHECKLIST SLIDES ---
    discipline_tax_color = '#FF4D4D' if broke_rules_pnl < 0 else '#888'
    slides.append({
        'type': 'stat', 
        'title': "The Discipline Tax ⚖️", 
        'value': f"${broke_rules_pnl:,.2f}", 
        'subtitle': "Total P&L from trades where you broke your rules. This is your 'lack of focus' cost.", 
        'value_style': f"color: {discipline_tax_color};"
    })

    system_pnl_color = '#00FF94' if followed_rules_pnl > 0 else '#FF4D4D'
    slides.append({
        'type': 'stat', 
        'title': "Professional Result 📈", 
        'value': f"${followed_rules_pnl:,.2f}", 
        'subtitle': "Total P&L from trades where you followed your rules. This is your system's true edge.", 
        'value_style': f"color: {system_pnl_color};"
    })

    # 3. Win Rate
    slides.append({'type': 'stat', 'title': "Precision", 'value': f"{win_rate}%", 'subtitle': f"You won {len(win_trades)} out of {count} trades.", 'value_style': 'color: #00d2ff;'})

    # 4. Profit Factor
    pf_color = '#00FF94' if profit_factor >= 1.5 else ('#FFD700' if profit_factor >= 1 else '#FF4D4D')
    slides.append({'type': 'stat', 'title': "Profit Factor", 'value': f"{profit_factor}", 'subtitle': f"For every $1 lost, you made ${profit_factor}.", 'value_style': f"color: {pf_color};"})

    # 5. Best Trade
    if best_trade and safe_pnl(best_trade) > 0:
        d_str = best_trade.open_time.strftime('%b %d') if best_trade.open_time else "Unknown Date"
        slides.append({'type': 'profit', 'title': "The Home Run 🚀", 'value': f"+${safe_pnl(best_trade):,.2f}", 'subtitle': f"{getattr(best_trade, 'symbol', 'Unknown')} • {d_str}<br>Your single best trade.", 'value_style': 'color: #00FF94;'})

    # 6. Worst Trade
    if worst_trade and safe_pnl(worst_trade) < 0:
        d_str = worst_trade.open_time.strftime('%b %d') if worst_trade.open_time else "Unknown Date"
        slides.append({'type': 'loss', 'title': "Tuition Fee 💸", 'value': f"-${abs(safe_pnl(worst_trade)):,.2f}", 'subtitle': f"{getattr(worst_trade, 'symbol', 'Unknown')} • {d_str}<br>Your biggest drawdown trade.", 'value_style': 'color: #FF4D4D;'})

    # 7. Avg Win vs Loss Chart
    win_h = 80; loss_h = 50
    if avg_win > 0: loss_h = min(100, (avg_loss / avg_win) * 80)
    avg_chart = f"<div style='display:flex; gap:30px; justify-content:center; margin-top:30px; align-items:flex-end; height:120px;'><div style='display:flex; flex-direction:column; align-items:center;'><div style='height:{win_h}px; width:50px; background:#00FF94; border-radius:5px;'></div><span style='margin-top:10px; color:#fff; font-weight:700;'>${avg_win:,.0f}</span><span style='font-size:10px; color:#888;'>AVG WIN</span></div><div style='display:flex; flex-direction:column; align-items:center;'><div style='height:{loss_h}px; width:50px; background:#FF4D4D; border-radius:5px;'></div><span style='margin-top:10px; color:#fff; font-weight:700;'>${avg_loss:,.0f}</span><span style='font-size:10px; color:#888;'>AVG LOSS</span></div></div>"
    slides.append({'type': 'chart', 'title': "Risk Appetite", 'value': f"1 : {rr_ratio}", 'subtitle': f"Risk/Reward Ratio.<br>{avg_chart}", 'value_style': 'color: #fff;'})

    # 8. Total Volume
    slides.append({'type': 'stat', 'title': "Total Volume", 'value': f"{total_lots:,.2f}", 'subtitle': "Total lots/contracts traded this month.", 'value_style': 'color: #e0e0e0;'})

    # 9. Fees & Commissions
    if abs(total_fees) > 0:
        slides.append({'type': 'stat', 'title': "The Taxman 🏦", 'value': f"-${abs(total_fees):,.2f}", 'subtitle': "Total paid in commissions and swaps.", 'value_style': 'color: #FF4D4D;'})

    # 10. Expectancy
    exp_color = '#00FF94' if expectancy > 0 else '#FF4D4D'
    slides.append({'type': 'stat', 'title': "Expectancy", 'value': f"${expectancy:,.2f}", 'subtitle': "Mathematical value of your next trade based on this month's data.", 'value_style': f"color: {exp_color};"})

    # 11. Month Split
    better_half = "First Half" if pnl_h1 > pnl_h2 else "Second Half"
    slides.append({'type': 'insight', 'title': "Month Split", 'value': better_half, 'subtitle': f"1st Half: ${pnl_h1:,.0f} | 2nd Half: ${pnl_h2:,.0f}<br>When you performed better.", 'value_style': 'color: #FF8C00;'})

    # 12. Streak Analysis
    if max_win_streak > 2:
        slides.append({'type': 'profit', 'title': "In The Zone 🔥", 'value': f"{max_win_streak} Wins", 'subtitle': "Your longest winning streak this month.<br>Unstoppable.", 'value_style': 'color: #FF8C00;'})
    elif max_loss_streak > 2:
        slides.append({'type': 'loss', 'title': "Rough Patch 🌧️", 'value': f"{max_loss_streak} Losses", 'subtitle': "Your longest losing streak.<br>Keep your head up.", 'value_style': 'color: #aaa;'})

    # 13. Speed Demon
    if shortest_trade_sec > 0:
        short_min = shortest_trade_sec / 60
        val_str = f"{short_min:.1f}m" if short_min >= 1 else f"{int(shortest_trade_sec)}s"
        slides.append({'type': 'stat', 'title': "Speed Demon ⚡", 'value': val_str, 'subtitle': "Your fastest round-trip trade.", 'value_style': 'color: #00d2ff;'})

    # 14. Diamond Hands
    if longest_trade_sec > 0:
        long_hr = longest_trade_sec / 3600
        val_str = f"{long_hr:.1f}h" if long_hr >= 1 else f"{int(longest_trade_sec/60)}m"
        slides.append({'type': 'stat', 'title': "Diamond Hands 💎", 'value': val_str, 'subtitle': "Your longest held position.", 'value_style': 'color: #b19cd9;'})

    # 15. Best Symbol
    if best_symbol_val > 0:
        slides.append({'type': 'profit', 'title': "Cash Cow 🐮", 'value': best_symbol_name, 'subtitle': f"You squeezed ${best_symbol_val:,.2f} out of this asset.", 'value_style': 'color: #00FF94; font-size: 55px;'})

    # 16. Worst Symbol
    if worst_symbol_val < 0:
        slides.append({'type': 'loss', 'title': "Your Kryptonite 💀", 'value': worst_symbol_name, 'subtitle': f"Cost you ${abs(worst_symbol_val):,.2f}.<br>Maybe avoid this one next month?", 'value_style': 'color: #FF4D4D; font-size: 55px;'})

    # 17. Most Traded
    if most_traded_sym != best_symbol_name and most_traded_sym != worst_symbol_name:
        slides.append({'type': 'stat', 'title': "The Obsession", 'value': most_traded_sym, 'subtitle': f"You traded this {symbol_count[most_traded_sym]} times.", 'value_style': 'color: #fff; font-size: 55px;'})

    # 18. Diversification
    slides.append({'type': 'stat', 'title': "Assets Traded", 'value': str(len(symbol_count)), 'subtitle': "Number of unique instruments traded.", 'value_style': 'color: #fff;'})

    # 19. Best Day
    slides.append({'type': 'stat', 'title': "Best Day", 'value': best_day, 'subtitle': f"{best_day}s were your most profitable days.<br>Total P&L: ${days_pnl.get(best_day, 0):,.2f}", 'value_style': 'color: #FFD700;'})

    # 20. Worst Day
    if worst_day != "N/A" and days_pnl.get(worst_day, 0) < 0:
        slides.append({'type': 'stat', 'title': "Worst Day", 'value': worst_day, 'subtitle': f"{worst_day}s were tough.<br>Total P&L: ${days_pnl.get(worst_day, 0):,.2f}", 'value_style': 'color: #aaa;'})

    # 21. Consistency
    profitable_days = len([d for d in days_pnl.values() if d > 0])
    total_days = len(days_pnl)
    consistency = int((profitable_days / total_days) * 100) if total_days > 0 else 0
    slides.append({'type': 'stat', 'title': "Consistency", 'value': f"{consistency}%", 'subtitle': f"You were profitable on {profitable_days} out of {total_days} active trading days.", 'value_style': 'color: #00d2ff;'})

    # 22. Golden Hour
    slides.append({'type': 'stat', 'title': "Golden Hour ⏰", 'value': best_hour_fmt, 'subtitle': f"You make the most money during this hour.<br>P&L: ${hours_pnl.get(best_hour, 0):,.2f}", 'value_style': 'color: #FFD700;'})

    # 23. Average Trade PnL
    avg_trade_val = total_pnl / count if count > 0 else 0
    at_color = '#00FF94' if avg_trade_val > 0 else '#FF4D4D'
    slides.append({'type': 'stat', 'title': "Avg Trade Value", 'value': f"${avg_trade_val:,.2f}", 'subtitle': "Average P&L per single trade execution.", 'value_style': f"color: {at_color};"})

    # 24. Trade Frequency
    trades_per_day = count / 20 
    freq_desc = "The Sniper 🎯" if trades_per_day < 2 else ("The Machine Gun 🔫" if trades_per_day > 5 else "Balanced ⚖️")
    slides.append({'type': 'insight', 'title': "Trade Frequency", 'value': freq_desc, 'subtitle': f"Avg {trades_per_day:.1f} trades per day.<br>Total: {count} trades.", 'value_style': 'color: #fff;'})

    # 25. Directional Bias
    longs = [t for t in trade_list if getattr(t, 'direction', getattr(t, 'type', '')) in ['LONG', 'BUY']]
    shorts = [t for t in trade_list if getattr(t, 'direction', getattr(t, 'type', '')) in ['SHORT', 'SELL']]
    long_pnl = sum(safe_pnl(t) for t in longs)
    short_pnl = sum(safe_pnl(t) for t in shorts)
    
    bias_val = "Balanced"
    if len(longs) > len(shorts) * 2: bias_val = "Permabull 🐂"
    elif len(shorts) > len(longs) * 2: bias_val = "Big Bear 🐻"
    
    slides.append({'type': 'chart', 'title': "Directional Bias", 'value': bias_val, 'subtitle': f"Longs P&L: ${long_pnl:,.0f}<br>Shorts P&L: ${short_pnl:,.0f}", 'value_style': 'color: #fff;'})

    # 26. Hold Time Insight
    avg_hold_win = 0
    if win_trades:
        valid_holds = [t for t in win_trades if t.close_time and t.open_time]
        if valid_holds:
            holds = [(t.close_time - t.open_time).total_seconds() for t in valid_holds]
            if holds: avg_hold_win = sum(holds) / len(holds)
    
    hold_insight = "Neutral"
    if avg_hold_win > 0:
        hold_insight = "Scalper ⚡" if avg_hold_win < 300 else "Swing Trader 🌊"

    slides.append({'type': 'insight', 'title': "Style Check", 'value': hold_insight, 'subtitle': f"Avg Win Hold: {int(avg_hold_win/60)} mins", 'value_style': 'color: #00d2ff;'})

    # 27. Total Pips (Approximation/Placeholder)
    max_lot = max((safe_val(t, 'lot_size') for t in trade_list), default=0)
    slides.append({'type': 'stat', 'title': "Heavy Hitter", 'value': f"{max_lot} Lots", 'subtitle': "The largest position size you took this month.", 'value_style': 'color: #e0e0e0;'})

    # 28. Emotional Analysis (AI Generated)
    emo_color = '#00FF94' if dominant_emotion in ['CALM', 'DISCIPLINED EXECUTION', 'CALM / FOCUSED'] else ('#FF4D4D' if 'TILTED' in dominant_emotion or 'ANXIOUS' in dominant_emotion or 'NERVOUS' in dominant_emotion else '#00d2ff')
    slides.append({'type': 'insight', 'title': "State of Mind", 'value': dominant_emotion, 'subtitle': "Your most frequent behavioral state this month.", 'value_style': f'color: {emo_color}; font-size: 36px; line-height: 1.2;'})

    # 29. Strategy Cluster (AI Generated)
    slides.append({'type': 'stat', 'title': "Dominant Strategy", 'value': dominant_strategy, 'subtitle': "The AI pattern recognition engine classified your primary execution style.", 'value_style': 'color: #b19cd9; font-size: 36px; line-height: 1.2;'})

    # MODULE D SLIDES
    slides.append({
        'type': 'insight', 
        'title': "PCA Variance Analysis", 
        'value': "The Math Behind the PnL", 
        'subtitle': pca_insight if pca_insight else "Insufficient data for Principal Component Analysis.", 
        'value_style': 'color: #00d2ff; font-size: 24px;'
    })

    slides.append({
        'type': 'insight', 
        'title': "Apriori Pattern Finder", 
        'value': "Hidden Edge Discovered", 
        'subtitle': hidden_pattern_insight, 
        'value_style': 'color: #FFD700; font-size: 24px;'
    })

    # 30. Final Verdict
    personality = "The Strategist"
    sub = "Consistent and calculated."
    if total_pnl < 0: personality = "The Grinder"; sub = "Rough month, but you're building resilience."
    elif profit_factor > 2.0: personality = "The Market Wizard"; sub = "Exceptional performance this month."
    elif win_rate > 70: personality = "The Sharpshooter"; sub = "High accuracy execution."
    elif count > 100: personality = "The Algorithm"; sub = "High volume, high energy."

    slides.append({'type': 'vibe', 'title': "Your Month's Vibe", 'value': personality, 'subtitle': sub, 'value_style': 'font-size: 40px; color: #fff; text-shadow: 0 0 20px rgba(255,255,255,0.5);'})

    return JsonResponse({'slides': slides})
@login_required
def checklist_view(request):
    # 1. Get Account Info (For Sidebar)
    # We allow this to be None so users can still use the checklist without a broker
    current_account, user_accounts = get_active_account(request)

    # 2. Get User's Strategies
    strategies = TradingStrategy.objects.filter(user=request.user)
    
    # If no strategy exists, create a default one automatically
    if not strategies.exists():
        default_strat = TradingStrategy.objects.create(user=request.user, name="My Default Strategy")
        # Refresh the queryset or create a list so the next block works
        strategies = [default_strat] 
    
    # 3. Determine Active Strategy
    active_strat_id = request.GET.get('strategy_id')
    
    if active_strat_id:
        active_strategy = get_object_or_404(TradingStrategy, id=active_strat_id, user=request.user)
    else:
        # Check if strategies is a list (from creation above) or a QuerySet
        if isinstance(strategies, list):
            active_strategy = strategies[0]
        else:
            active_strategy = strategies.first()

    # 4. Get Rules
    rules = active_strategy.rules.all().order_by('created_at')

    context = {
        'strategies': strategies,
        'active_strategy': active_strategy,
        'rules': rules,
        'current_account': current_account,  # <--- CRITICAL: Added for Sidebar
    }
    return render(request, 'checklist.html', context)

@login_required
@require_POST
def add_rule(request):
    try:
        data = json.loads(request.body)
        text = data.get('text')
        strategy_id = data.get('strategy_id') # We now need this!
        
        if text and strategy_id:
            strategy = get_object_or_404(TradingStrategy, id=strategy_id, user=request.user)
            rule = ChecklistRule.objects.create(strategy=strategy, text=text)
            return JsonResponse({'status': 'success', 'id': rule.id, 'text': rule.text})
        return JsonResponse({'status': 'error', 'message': 'Missing data'}, status=400)
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

@login_required
@require_POST
def delete_rule(request, rule_id):
    # CHANGED: Use 'strategy__user' to check ownership safely
    rule = get_object_or_404(ChecklistRule, id=rule_id, strategy__user=request.user)
    rule.delete()
    return JsonResponse({'status': 'success'})

@login_required
@require_POST
def edit_rule(request, rule_id):
    try:
        data = json.loads(request.body)
        new_text = data.get('text')
        
        # CHANGED: Use 'strategy__user' here too
        rule = get_object_or_404(ChecklistRule, id=rule_id, strategy__user=request.user)
        
        if new_text:
            rule.text = new_text
            rule.save() # Yes, this commits the change to the DB
            return JsonResponse({'status': 'success', 'text': rule.text})
        return JsonResponse({'status': 'error', 'message': 'No text provided'}, status=400)
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
    
@login_required
@require_POST
def add_strategy(request):
    try:
        data = json.loads(request.body)
        name = data.get('name')
        if name:
            strat = TradingStrategy.objects.create(user=request.user, name=name)
            return JsonResponse({'status': 'success', 'id': strat.id, 'name': strat.name})
    except:
        pass
    return JsonResponse({'status': 'error'})

@login_required
@require_POST
def rename_strategy(request, strategy_id):
    try:
        data = json.loads(request.body)
        new_name = data.get('name')
        
        strategy = get_object_or_404(TradingStrategy, id=strategy_id, user=request.user)
        
        if new_name:
            strategy.name = new_name
            strategy.save()
            return JsonResponse({'status': 'success', 'name': strategy.name})
        return JsonResponse({'status': 'error', 'message': 'No name provided'}, status=400)
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

@login_required
@require_POST
def delete_strategy(request, strategy_id):
    strategy = get_object_or_404(TradingStrategy, id=strategy_id, user=request.user)
    
    # Optional: Prevent deleting the last remaining strategy
    if TradingStrategy.objects.filter(user=request.user).count() <= 1:
        return JsonResponse({'status': 'error', 'message': 'Cannot delete your only strategy.'}, status=400)
        
    strategy.delete()
    return JsonResponse({'status': 'success'})