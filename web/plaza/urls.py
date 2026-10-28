from django.urls import path

from . import views

from . import scope_pages

urlpatterns = [
    path("", lambda request: scope_pages.render_scope(request, "home"), name="home"),
    path("leaderboard/", lambda request: scope_pages.render_scope(request, "board"), name="leaderboard"),
    path("analysis/", lambda request: scope_pages.render_scope(request, "analysis"), name="analysis"),
    path("pricing/", lambda request: scope_pages.render_scope(request, "pricing"), name="pricing"),
    path("wallet/", lambda request: scope_pages.render_scope(request, "wallet"), name="wallet"),
    path("api/tape/", scope_pages.tape_feed, name="tape_feed"),
    path("api/tape/test/", scope_pages.tape_test, name="tape_test"),
    path("api/wallet/", scope_pages.wallet_api, name="wallet_api"),
    path("api/membership/", scope_pages.membership, name="membership"),
    path("api/subscribe/", scope_pages.subscribe, name="subscribe"),
    path("auth/telegram/callback", scope_pages.telegram_callback, name="telegram_callback"),
    path("auth/telegram/start/", scope_pages.telegram_start, name="telegram_start"),
    path("auth/telegram/", scope_pages.telegram_login, name="telegram_login"),
    path("auth/wallet/nonce/", scope_pages.wallet_nonce, name="wallet_nonce"),
    path("auth/wallet/verify/", scope_pages.wallet_verify, name="wallet_verify"),
    path("board/", views.board, name="board"),
    path("signals/", views.signals_page, name="signals"),
    path("signals/feed/", views.signal_feed, name="signal_feed"),
    path("leaders/<int:leader_id>/", views.detail, name="detail"),
    path("leaders/<int:leader_id>/follow/", views.follow, name="follow"),
    path("wallets/", views.wallets_page, name="wallets"),
    path("wallets/add/", views.wallet_add, name="wallet_add"),
    path("wallets/<int:leader_id>/toggle/", views.wallet_toggle, name="wallet_toggle"),
    path("wallets/<int:leader_id>/delete/", views.wallet_delete, name="wallet_delete"),
    path("accounts/", views.accounts_page, name="accounts"),
    path("accounts/add/", views.account_add, name="account_add"),
    path("accounts/<int:account_id>/toggle/", views.account_toggle, name="account_toggle"),
    path("accounts/<int:account_id>/delete/", views.account_delete, name="account_delete"),
    path("me/", views.me, name="me"),
    path("invite/", views.invite, name="invite"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("register/", views.register_view, name="register"),
]
