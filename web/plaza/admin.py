from django.contrib import admin

from .models import Commission, ExchangeAccount, Follow, Leader, Profile


@admin.register(Leader)
class LeaderAdmin(admin.ModelAdmin):
    list_display = ("name", "address", "source", "enabled", "created_at")
    search_fields = ("name", "address")


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "invite_code", "invited_by")


@admin.register(ExchangeAccount)
class ExchangeAccountAdmin(admin.ModelAdmin):
    list_display = ("user", "name", "venue", "key_last4", "enabled")
    exclude = ("key_enc", "secret_enc", "key_fp")

    def has_add_permission(self, request) -> bool:
        return False


@admin.register(Follow)
class FollowAdmin(admin.ModelAdmin):
    list_display = ("user", "leader", "account", "active", "updated_at")


@admin.register(Commission)
class CommissionAdmin(admin.ModelAdmin):
    list_display = ("beneficiary", "source_user", "amount", "rate", "status", "created_at")
