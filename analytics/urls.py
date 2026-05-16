from django.urls import path
from . import views
from django.contrib.auth import views as auth_views
from .views import CustomResetView 

urlpatterns = [
    # --- Authentication ---
    path('register/', views.register_view, name='register'),
    path('login/', views.login_view, name='login'),
    
    # NEW: Logout Route (Redirects to Login page)
    path('logout/', auth_views.LogoutView.as_view(next_page='login'), name='logout'),

    path('', views.login_view, name='home'),
    
    # --- Main App Views ---
    path('dashboard/', views.dashboard, name='dashboard'),
    path('journal/', views.journal, name='journal'),
    
    # NEW: Manual Import Route
    path('import-trades/', views.import_trades, name='import_trades'),

    path('live-monitor/', views.live_monitor, name='live_monitor'),
    path('calculator/', views.lot_calculator, name='lot_calculator'),
    path('settings/', views.settings_view, name='settings'),
    path('settings/delete-user/', views.delete_user_account, name='delete_user_account'),    path('profile/', views.profile_view, name='profile'),
    
    # --- Account Management ---
    path('add-account/', views.add_account, name='add_account'),
    path('delete-account/<int:account_id>/', views.delete_trading_account, name='delete_account'),
    path('sync-trades/', views.sync_trading_data, name='sync_trades'),
    path('account/delete/<int:account_id>/', views.delete_trading_account, name='delete_account'),
    
    # --- Reports & Details ---
    path('trade/<str:trade_id>/', views.trade_detail, name='trade_detail'),
    path('daily-trades/<int:year>/<int:month>/<int:day>/', views.daily_trades, name='daily_trades'),
    
    # --- Password Reset ---
    # 1. Request Page (UPDATED to use Custom View)
    path('password-reset/', CustomResetView.as_view(), name='password_reset'),

    # 2. Email Sent Page (Standard)
    path('password-reset/done/', auth_views.PasswordResetDoneView.as_view(
        template_name='password_change.html',
        extra_context={'mode': 'sent'}
    ), name='password_reset_done'),

    # 3. Confirm Page (Standard)
    path('password-reset-confirm/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(
        template_name='password_change.html',
        extra_context={'mode': 'reset'}
    ), name='password_reset_confirm'),

    # 4. Complete Page (Standard)
    path('password-reset-complete/', auth_views.PasswordResetCompleteView.as_view(
        template_name='password_change.html',
        extra_context={'mode': 'done'}
    ), name='password_reset_complete'),
    path('api/ai-predict/', views.ai_predict_trade, name='ai_predict_trade'),

    path('api/send-verification-code/', views.send_verification_code, name='send_verification_code'),
    path('api/verify-email-code/', views.verify_code, name='verify_email_code'),

    path('api/send-reset-code/', views.send_reset_code, name='send_reset_code'),
    path('api/verify-reset-code/', views.verify_reset_code, name='verify_reset_code'),
    path('api/complete-password-reset/', views.complete_password_reset, name='complete_password_reset'),

    path('reports/', views.reports_dashboard, name='reports'),
    path('api/reports/story/<int:year>/<int:month>/', views.get_report_story, name='report_story_api'),

    path('checklist/', views.checklist_view, name='checklist'),
    path('api/checklist/add/', views.add_rule, name='add_rule'),
    path('api/checklist/delete/<int:rule_id>/', views.delete_rule, name='delete_rule'),
    path('api/checklist/edit/<int:rule_id>/', views.edit_rule, name='edit_rule'),
    path('api/strategy/add/', views.add_strategy, name='add_strategy'),
    path('api/strategy/rename/<int:strategy_id>/', views.rename_strategy, name='rename_strategy'),
    path('api/strategy/delete/<int:strategy_id>/', views.delete_strategy, name='delete_strategy'),
]