from django.contrib import admin

from .models import Commission, Follow, Leader, Profile


@admin.register(Leader)
class LeaderAdmin(admin.ModelAdmin):
    list_display = ("name", "address", "enabled", "created_at")
    search_fields = ("name", "address")


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "invite_code", "invited_by")


@admin.register(Follow)
class FollowAdmin(admin.ModelAdmin):
    list_display = ("user", "leader", "active", "updated_at")


@admin.register(Commission)
class CommissionAdmin(admin.ModelAdmin):
    list_display = ("beneficiary", "source_user", "amount", "rate", "status", "created_at")
