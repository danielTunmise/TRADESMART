import hashlib
import io
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta

import pandas as pd
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth.models import User

from .models import TradingAccount, Trade, UserProfile, TradeNote, AccountBalance

# --- MOCK DATA ---
# This data simulates responses from the SnapTrade API

def create_mock_api_order(
    symbol="AAPL", action="BUY", qty=10, price=150.0, 
    time_str=None, order_id=None
):
    """Helper to create a single mock API order dictionary."""
    if time_str is None:
        time_str = timezone.now().isoformat()
    if order_id is None:
        order_id = f"ord_{hashlib.md5(time_str.encode()).hexdigest()[:10]}"

    return {
        'id': order_id,
        'symbol': {'symbol': symbol, 'raw_symbol': symbol},
        'action': action,
        'total_quantity': qty,
        'filled_quantity': qty,
        'execution_price': price,
        'price': price,
        'time_placed': time_str,
        'time_executed': time_str,
        'stop_loss': 0.0,
        'take_profit': 0.0,
    }

MOCK_API_ORDERS = [
    create_mock_api_order("AAPL", "BUY", 10, 150.0, (timezone.now() - timedelta(days=2)).isoformat()),
    create_mock_api_order("AAPL", "SELL", 10, 155.0, (timezone.now() - timedelta(days=1)).isoformat()),
    create_mock_api_order("GOOG", "BUY", 5, 2800.0, (timezone.now() - timedelta(hours=5)).isoformat()),
]

MOCK_API_POSITIONS = [
    {
        'symbol': {'symbol': 'TSLA', 'raw_symbol': 'TSLA'},
        'units': 100.0,
        'price': 250.0,
        'open_pnl': 1250.50
    }
]

# --- BASE TEST CASE ---

class BaseViewTestCase(TestCase):
    """Sets up a common environment for view tests."""
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='testuser', 
            password='testpassword123', 
            email='test@example.com'
        )
        self.profile = UserProfile.objects.create(user=self.user, risk_appetite='moderate')
        
        # Two accounts: one manual, one for API simulation
        self.manual_account = TradingAccount.objects.create(
            user=self.user, 
            broker_name="Manual Account",
            account_number="MANUAL123"
        )
        self.api_account = TradingAccount.objects.create(
            user=self.user, 
            broker_name="API Account", 
            snaptrade_user_id="snap_user_123", 
            user_secret="snap_secret_abc"
        )
        
        # Log in the client for authenticated tests
        self.client.login(username='testuser', password='testpassword123')

        # Set the manual account as active in the session
        session = self.client.session
        session['active_account_id'] = self.manual_account.id
        session.save()

# --- AUTHENTICATION TESTS ---

class AuthViewsTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='testuser', password='password123', email='test@example.com')

    def test_register_view(self):
        # Test GET
        response = self.client.get(reverse('register'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'register.html')

        # Test POST - success
        response = self.client.post(reverse('register'), {
            'username': 'newuser',
            'email': 'new@example.com',
            'password': 'newpassword123',
            'password_confirm': 'newpassword123',
            'first_name': 'New',
            'last_name': 'User'
        }, follow=True)
        self.assertRedirects(response, reverse('login'))
        self.assertTrue(User.objects.filter(username='newuser').exists())

    def test_login_view(self):
        # Test GET
        response = self.client.get(reverse('login'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'login.html')

        # Test POST with username - success
        response = self.client.post(reverse('login'), {'username': 'testuser', 'password': 'password123'}, follow=True)
        self.assertRedirects(response, reverse('dashboard'))
        self.assertTrue(response.context['user'].is_authenticated)

        # Test POST with email - success
        self.client.logout()
        response = self.client.post(reverse('login'), {'username': 'test@example.com', 'password': 'password123'}, follow=True)
        self.assertRedirects(response, reverse('dashboard'))
        self.assertTrue(response.context['user'].is_authenticated)

# --- CORE VIEW TESTS ---

@patch('analytics.cloud_connector.TradeSmartCloud')
class CoreViewsTestCase(BaseViewTestCase):

    def setUp(self):
        super().setUp()
        # Create a sample trade for the manual account
        self.trade = Trade.objects.create(
            user=self.user,
            account=self.manual_account,
            trade_id="test_trade_001",
            symbol="BTCUSD",
            direction="BUY",
            lot_size=1.0,
            open_price=50000,
            close_price=51000,
            profit=1000,
            open_time=timezone.now() - timedelta(days=1),
            close_time=timezone.now()
        )

    def test_dashboard_view_manual_account(self, mock_cloud_connector):
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'dashboard.html')
        self.assertEqual(response.context['current_account'], self.manual_account)
        self.assertGreater(response.context['global_stats']['net_pnl'], 0)
        self.assertEqual(response.context['global_stats']['win_rate'], 100)

    def test_dashboard_view_api_account(self, mock_cloud_connector):
        # Configure mock
        mock_instance = mock_cloud_connector.return_value
        mock_instance.get_accounts.return_value = [{'id': 'cloud_acc_123'}]
        mock_instance.api.account_information.get_user_account_orders.return_value.body = MOCK_API_ORDERS
        mock_instance.api.account_information.get_user_account_balance.return_value.body = [{'cash': 50000.0}]

        # Switch active account to API account
        session = self.client.session
        session['active_account_id'] = self.api_account.id
        session.save()

        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        # Check that the API data was processed (AAPL trade profit is $50)
        self.assertAlmostEqual(response.context['global_stats']['net_pnl'], 50.0)
        self.assertEqual(response.context['current_account'], self.api_account)

    def test_journal_view(self, mock_cloud_connector):
        response = self.client.get(reverse('journal'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'journal.html')
        self.assertEqual(len(response.context['page_obj']), 1)
        self.assertEqual(response.context['page_obj'][0]['symbol'], 'BTCUSD')

    def test_trade_detail_view_db(self, mock_cloud_connector):
        response = self.client.get(reverse('trade_detail', args=[self.trade.trade_id]))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'trade_detail.html')
        self.assertEqual(response.context['trade']['symbol'], 'BTCUSD')

    def test_trade_detail_add_note(self, mock_cloud_connector):
        url = reverse('trade_detail', args=[self.trade.trade_id])
        response = self.client.post(url, {'note': 'This is a test note.'}, follow=True)
        self.assertRedirects(response, url)
        self.assertTrue(TradeNote.objects.filter(trade=self.trade, content='This is a test note.').exists())

# --- DATA IMPORT AND MANAGEMENT TESTS ---

class DataImportTestCase(BaseViewTestCase):
    def test_import_trades_view_get(self):
        response = self.client.get(reverse('import_trades'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'import_trades.html')

    def test_import_trades_success(self):
        # Create a mock CSV file in memory
        csv_data = (
            "Time,Symbol,Type,Volume,Open Price,Close Price,Profit
"
            "2023-10-26 10:00:00,EURUSD,buy,0.1,1.05,1.06,100
"
            "2023-10-26 11:00:00,USDJPY,sell,0.5,150,149.5,250
"
        )
        csv_file = io.StringIO(csv_data)
        csv_file.name = 'trades_MANUAL123.csv' # Name matches account number

        response = self.client.post(reverse('import_trades'), {
            'file': csv_file
        }, follow=True)

        self.assertRedirects(response, reverse('journal'))
        messages = list(response.context['messages'])
        self.assertEqual(len(messages), 1)
        self.assertIn('Added 2 new trades', str(messages[0]))
        self.assertEqual(Trade.objects.filter(account=self.manual_account).count(), 2)

    def test_import_trades_account_lock_fail(self):
        # Name in filename does NOT match the locked account number
        csv_data = "Time,Symbol,Profit
2023-10-26,FAKE,100"
        csv_file = io.StringIO(csv_data)
        csv_file.name = 'trades_WRONGID999.csv' 

        response = self.client.post(reverse('import_trades'), {'file': csv_file}, follow=True)

        self.assertRedirects(response, reverse('import_trades'))
        messages = list(response.context['messages'])
        self.assertEqual(len(messages), 1)
        self.assertIn('Security Block', str(messages[0]))
        self.assertEqual(Trade.objects.count(), 0) # No trades should be imported

    def test_lazy_account_creation_on_import(self):
        # Log out and log back in to clear session
        self.client.logout()
        self.client.login(username='testuser', password='testpassword123')
        
        # Set session variable to simulate coming from 'add_account' view
        session = self.client.session
        session['pending_manual_name'] = 'Lazy Created Account'
        session.save()

        csv_data = "Time,Symbol,Profit
2023-10-26,LAZY,500"
        csv_file = io.StringIO(csv_data)
        csv_file.name = 'trades_LAZYID456.csv'

        self.client.post(reverse('import_trades'), {'file': csv_file}, follow=True)
        
        # Check if the new account was created
        self.assertTrue(TradingAccount.objects.filter(broker_name='Lazy Created Account').exists())
        new_account = TradingAccount.objects.get(broker_name='Lazy Created Account')
        self.assertEqual(new_account.account_number, 'LAZYID456')
        self.assertEqual(Trade.objects.filter(account=new_account).count(), 1)


# --- LIVE MONITOR AND API-HEAVY TESTS ---

@patch('analytics.cloud_connector.TradeSmartCloud')
class LiveViewsTestCase(BaseViewTestCase):
    def setUp(self):
        super().setUp()
        # Switch to the API account for these tests
        session = self.client.session
        session['active_account_id'] = self.api_account.id
        session.save()
    
    def test_live_monitor_view(self, mock_cloud_connector):
        # Configure mock
        mock_instance = mock_cloud_connector.return_value
        mock_instance.get_accounts.return_value = [{'id': 'cloud_acc_123'}]
        mock_instance.fetch_positions.return_value = {'positions': MOCK_API_POSITIONS}

        response = self.client.get(reverse('live_monitor'))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'live_monitor.html')
        self.assertAlmostEqual(response.context['floating_pnl'], 1250.50)
        self.assertEqual(len(response.context['open_trades']), 1)
        self.assertEqual(response.context['open_trades'][0]['name'], 'TSLA')

    def test_live_monitor_no_connection(self, mock_cloud_connector):
        # Simulate API failure
        mock_instance = mock_cloud_connector.return_value
        mock_instance.get_accounts.return_value = [] # No accounts found

        response = self.client.get(reverse('live_monitor'))
        self.assertEqual(response.status_code, 200)
        # Check that it renders the "locked" page when no connection
        self.assertTemplateUsed(response, 'live_monitor_locked.html')
    
    def test_live_monitor_add_note(self, mock_cloud_connector):
        response = self.client.post(reverse('live_monitor'), {
            'symbol': 'SPY',
            'qty': '10',
            'note': 'Live entry note',
            'emotion': 'calm'
        }, follow=True)
        self.assertRedirects(response, reverse('live_monitor'))
        # Check that a placeholder Trade object was created for the note
        self.assertTrue(Trade.objects.filter(symbol='SPY', strategy_tag='Live Monitor Log').exists())
        note = TradeNote.objects.get(content='Live entry note')
        self.assertEqual(note.emotion_tag, 'calm')
        self.assertEqual(note.trade.symbol, 'SPY')

# --- SETTINGS AND MANAGEMENT TESTS ---
class SettingsViewsTestCase(BaseViewTestCase):

    def test_settings_view_get(self):
        response = self.client.get(reverse('settings'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'settings.html')
        self.assertIn('form-control', response.content.decode())

    def test_settings_view_post(self):
        response = self.client.post(reverse('settings'), {
            'first_name': 'Updated First',
            'last_name': 'Updated Last',
            'email': 'updated@example.com',
            'risk_appetite': 'aggressive'
        }, follow=True)
        self.assertRedirects(response, reverse('settings'))
        
        # Verify user and profile were updated in the database
        self.user.refresh_from_db()
        self.profile.refresh_from_db()
        self.assertEqual(self.user.first_name, 'Updated First')
        self.assertEqual(self.user.email, 'updated@example.com')
        self.assertEqual(self.profile.risk_appetite, 'aggressive')

    @patch('analytics.cloud_connector.TradeSmartCloud.delete_snaptrade_user', return_value=True)
    def test_delete_trading_account(self, mock_delete_user):
        account_id_to_delete = self.api_account.id
        self.assertTrue(TradingAccount.objects.filter(id=account_id_to_delete).exists())

        response = self.client.post(reverse('delete_trading_account', args=[account_id_to_delete]), follow=True)
        
        self.assertRedirects(response, reverse('settings'))
        # Verify the mock API call was made
        mock_delete_user.assert_called_once_with(self.api_account.snaptrade_user_id)
        # Verify the account was deleted from the DB
        self.assertFalse(TradingAccount.objects.filter(id=account_id_to_delete).exists())
        messages = list(response.context['messages'])
        self.assertIn('Successfully disconnected', str(messages[0]))
