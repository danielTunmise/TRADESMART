import os
import asyncio
import nest_asyncio
from snaptrade_client import SnapTrade
from datetime import datetime



class TradeSmartCloud:
    def __init__(self):
        # ---------------------------------------------------
        # API KEYS
        # ---------------------------------------------------
        self.snap_client_id = os.getenv("SNAPTRADE_CLIENT_ID", "TRADESMART-TEST-APNRW")
        self.snap_consumer_key = os.getenv("SNAPTRADE_CONSUMER_KEY", "VkvPq2XCJxbKoEiGZSX31WzUpEvnCEfLjux7YwLi5dFmCG3MQE")
        
        # MetaApi Token
        self.meta_token = os.getenv("METAAPI_TOKEN", "YOUR_METAAPI_TOKEN_HERE")
        # ---------------------------------------------------
        
        # Initialize SnapTrade
        try:
            self.api = SnapTrade(
                client_id=self.snap_client_id, 
                consumer_key=self.snap_consumer_key
            )
        except Exception as e:
            print(f"⚠️ SnapTrade Init Error: {e}")

    # --- SNAPTRADE METHODS (UNCHANGED) ---
    def test_connection(self):
        try:
            status = self.api.api_status.check()
            if status: return True
        except: return False
        return False

    def register_user(self, username):
        try:
            user_data = self.api.authentication.register_snap_trade_user(user_id=username)
            return user_data.body['userId'], user_data.body['userSecret']
        except: return None, None

    def generate_login_link(self, user_id, user_secret, redirect_uri=None):
        try:
            kwargs = {"user_id": user_id, "user_secret": user_secret}
            if redirect_uri:
                kwargs["immediate_redirect"] = True
                kwargs["custom_redirect"] = redirect_uri
            response = self.api.authentication.login_snap_trade_user(**kwargs)
            data = response.body
            if isinstance(data, dict):
                return data.get('redirectURI') or data.get('loginRedirectURI')
            return getattr(data, 'redirectURI', getattr(data, 'loginRedirectURI', None))
        except: return None

    def get_accounts(self, user_id, user_secret):
        if user_secret == "DIRECT_CONN" or user_secret == "METAAPI_ACTIVE": return [] 
        try:
            return self.api.account_information.list_user_accounts(user_id=user_id, user_secret=user_secret).body
        except: return []

    def fetch_positions(self, user_id, user_secret, account_id):
        if user_secret == "DIRECT_CONN" or user_secret == "METAAPI_ACTIVE": return [] 
        try:
            return self.api.account_information.get_user_holdings(user_id=user_id, user_secret=user_secret, account_id=account_id).body
        except: return []
        
    def fetch_history(self, user_id, user_secret, account_id):
        if user_secret == "DIRECT_CONN" or user_secret == "METAAPI_ACTIVE": return []
        try:
            return self.api.account_information.list_user_activities(user_id=user_id, user_secret=user_secret, account_id=account_id).body
        except: return []

    def delete_snaptrade_user(self, user_id):
        try:
            self.api.authentication.delete_snap_trade_user(user_id=user_id)
            return True
        except: return False


   