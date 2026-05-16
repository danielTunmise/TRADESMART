from django.contrib import admin
from .models import UserProfile, Trade, TradeNote, AccountBalance, ChecklistRule, TradingStrategy

# This tells the Admin Panel: "Show me these tables!"
admin.site.register(UserProfile)
admin.site.register(Trade)
admin.site.register(TradeNote)
admin.site.register(AccountBalance)  # <--- Add this line

@admin.register(TradingStrategy)
class TradingStrategyAdmin(admin.ModelAdmin):
    list_display = ('name', 'user', 'created_at')
    search_fields = ('name', 'user__username')

# Updated Rule Admin
@admin.register(ChecklistRule)
class ChecklistRuleAdmin(admin.ModelAdmin):
    # CHANGED: 'user' -> 'strategy' (since rules now belong to strategies)
    list_display = ('text', 'strategy', 'created_at')
    
    # CHANGED: Search by strategy name or username via strategy
    search_fields = ('text', 'strategy__name', 'strategy__user__username')
    
    list_filter = ('created_at', 'strategy')