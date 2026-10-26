from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("signals/", views.signals_page, name="signals"),
    path("signals/feed/", views.signal_feed, name="signal_feed"),
    path("leaders/<int:leader_id>/", views.detail, name="detail"),
    path("leaders/<int:leader_id>/follow/", views.follow, name="follow"),
    path("wallets/", views.wallets_page, name="wallets"),
    path("wallets/add/", views.wallet_add, name="wallet_add"),
    path("wallets/<int:leader_id>/toggle/", views.wallet_toggle, name="wallet_toggle"),
    path("me/", views.me, name="me"),
    path("invite/", views.invite, name="invite"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("register/", views.register_view, name="register"),
]
