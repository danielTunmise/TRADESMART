from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.forms import UserCreationForm
from .models import UserProfile, TradingAccount
from django.contrib.auth.forms import PasswordResetForm

# --- 1. REGISTRATION FORM ---
class UserRegistrationForm(UserCreationForm):
    # We only ask for Risk Appetite here. 
    # Trading Account details are now handled separately after login.
    risk_appetite = forms.ChoiceField(choices=UserProfile.RISK_CHOICES)

    class Meta:
        model = User
        fields = ['username', 'email', 'first_name', 'last_name']

    def save(self, commit=True):
        # 1. Save the User (Username/Password)
        user = super().save(commit=False)
        if commit:
            user.save()
            
            # 2. Save the Profile (Risk Appetite)
            profile, created = UserProfile.objects.get_or_create(user=user)
            profile.risk_appetite = self.cleaned_data['risk_appetite']
            profile.save()
        return user

# --- 2. PROFILE SETTINGS FORM ---
class ProfileUpdateForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ['risk_appetite', 'daily_loss_limit', 'avatar']
        widgets = {
            'risk_appetite': forms.Select(attrs={'class': 'form-input'}),
            'daily_loss_limit': forms.NumberInput(attrs={'class': 'form-input'}),
            'avatar': forms.FileInput(attrs={'class': 'form-input'}),
        }

# --- 3. TRADING ACCOUNT FORM (For "Add Account" Page) ---
class TradingAccountForm(forms.ModelForm):
    class Meta:
        model = TradingAccount
        # We only ask for a nickname/broker name. 
        # The technical IDs (user_id, secret) are handled automatically in the background.
        fields = ['broker_name']
        
        widgets = {
            'broker_name': forms.TextInput(attrs={'placeholder': 'e.g. My Robinhood Account'}),
        }

# --- 4. TRADE IMPORT FORM (For Manual Imports) ---
class TradeImportForm(forms.Form):
    BROKER_CHOICES = [
        ('mt4_mt5', 'MetaTrader 4/5 (Deriv, FBS, etc.)'),
        ('binance', 'Binance / ByBit (Crypto)'),
        ('generic', 'Generic CSV (Date, Symbol, Type, Lots, Entry, Exit, PnL)'),
    ]
    
    broker_format = forms.ChoiceField(
        choices=BROKER_CHOICES, 
        label="Broker Platform",
        widget=forms.Select(attrs={
            'style': 'width:100%; background:#0D0D0D; border:1px solid #333; color:white; padding:12px; border-radius:8px; outline:none;'
        })
    )
    
    file = forms.FileField(
        label="Upload History File",
        widget=forms.FileInput(attrs={
            'style': 'width:100%; background:#0D0D0D; border:1px solid #333; color:white; padding:12px; border-radius:8px;'
        })
    )

class CustomPasswordResetForm(PasswordResetForm):
    def clean_email(self):
        email = self.cleaned_data.get('email')
        
        # The Custom Logic: Check if email exists in DB
        if not User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("We could not find an account associated with this email address.")
            
        return email