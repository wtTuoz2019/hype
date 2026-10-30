import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
HYPE_ROOT = BASE_DIR.parent
if str(HYPE_ROOT) not in sys.path:
    sys.path.insert(0, str(HYPE_ROOT))

SECRET_PATH = BASE_DIR / "data" / "django.secret"
SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
if SECRET_PATH.exists():
    SECRET_KEY = SECRET_PATH.read_text(encoding="utf-8").strip()
else:
    import secrets

    SECRET_KEY = secrets.token_hex(32)
    SECRET_PATH.write_text(SECRET_KEY, encoding="utf-8")

DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get(
        "DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost,testserver"
    ).split(",")
    if host.strip()
]

INSTALLED_APPS = [
    "jazzmin",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "plaza",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.gzip.GZipMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
]

# 本机 3306 当前没开。设 HYPE_DB=mysql 且 MySQL 可连时走 db_bihao，否则用本机 SQLite。
if os.environ.get("HYPE_DB") == "mysql":
    import pymysql

    pymysql.install_as_MySQLdb()
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.mysql",
            "NAME": os.environ.get("MYSQL_DATABASE", "db_bihao"),
            "USER": os.environ.get("MYSQL_USER", "root"),
            "PASSWORD": os.environ.get("MYSQL_PASSWORD", os.environ.get("MYSQL_PASS", "")),
            "HOST": os.environ.get("MYSQL_HOST", "127.0.0.1"),
            "PORT": os.environ.get("MYSQL_PORT", "3306"),
            "OPTIONS": {"charset": "utf8mb4"},
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "data" / "copy.sqlite3",
            "OPTIONS": {"timeout": 30},
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
]

LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATICFILES_DIRS = [HYPE_ROOT / "alphascope-simple"]
STATIC_ROOT = BASE_DIR / "staticfiles"
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("DJANGO_CSRF_ORIGINS", "").split(",")
    if origin.strip()
]
if os.environ.get("DJANGO_BEHIND_HTTPS") == "1":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SESSION_COOKIE_SAMESITE = "Lax"
# 允许 Telegram 授权小窗保持 window.opener，否则登完主页收不到会话
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin-allow-popups"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "/login/"
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_BOT_USERNAME = os.environ.get("TELEGRAM_BOT_USERNAME", "")
# 开单快讯推群：把机器人拉进群后填 chat_id（群一般为 -100 开头）
TELEGRAM_NOTIFY_CHAT_ID = os.environ.get("TELEGRAM_NOTIFY_CHAT_ID", "")
TELEGRAM_SITE_URL = os.environ.get("TELEGRAM_SITE_URL", "https://p.c7.pro")
TELEGRAM_NOTIFY_LABELS = os.environ.get("TELEGRAM_NOTIFY_LABELS", "")  # 空=全部；如 开多,开空,加多,加空
# Pro 资格群：默认复用推送群；私密群务必填邀请链接
TELEGRAM_PRO_CHAT_ID = os.environ.get("TELEGRAM_PRO_CHAT_ID", "")
TELEGRAM_PRO_INVITE_URL = os.environ.get("TELEGRAM_PRO_INVITE_URL", "")
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"

JAZZMIN_SETTINGS = {
    "site_title": "AlphaScope",
    "site_header": "AlphaScope",
    "site_brand": "AlphaScope",
    "welcome_sign": "钱包跟踪管理后台",
    "copyright": "AlphaScope",
    "search_model": ["plaza.Leader", "auth.User"],
    "topmenu_links": [
        {"name": "打开前台", "url": "/", "new_window": True},
        {"name": "跟踪钱包", "url": "admin:plaza_leader_changelist"},
        {"name": "开单信号", "url": "admin:plaza_tradesignal_changelist"},
    ],
    "show_sidebar": True,
    "navigation_expanded": True,
    "hide_apps": [],
    "hide_models": [],
    "order_with_respect_to": [
        "plaza",
        "plaza.Leader",
        "plaza.TradeSignal",
        "plaza.LeaderSnapshot",
        "plaza.AlertSub",
        "plaza.Profile",
        "plaza.Follow",
        "plaza.ExchangeAccount",
        "plaza.Commission",
        "auth",
    ],
    "icons": {
        "auth": "fas fa-users-cog",
        "auth.user": "fas fa-user",
        "auth.Group": "fas fa-users",
        "plaza.Leader": "fas fa-wallet",
        "plaza.TradeSignal": "fas fa-bolt",
        "plaza.LeaderSnapshot": "fas fa-chart-line",
        "plaza.Profile": "fas fa-id-card",
        "plaza.Follow": "fas fa-link",
        "plaza.ExchangeAccount": "fas fa-key",
        "plaza.AlertSub": "fas fa-bell",
        "plaza.Commission": "fas fa-coins",
    },
    "default_icon_parents": "fas fa-folder",
    "default_icon_children": "fas fa-circle",
    "related_modal_active": True,
    "custom_css": None,
    "custom_js": None,
    "use_google_fonts_cdn": True,
    "show_ui_builder": False,
    "changeform_format": "horizontal_tabs",
    "language_chooser": False,
}

JAZZMIN_UI_TWEAKS = {
    "theme": "flatly",
    "default_theme_mode": "light",
    "navbar": "navbar-white navbar-light",
    "sidebar": "sidebar-dark-primary",
    "sidebar_nav_child_indent": True,
    "sidebar_nav_compact_style": True,
    "accent": "accent-primary",
    "actions_sticky_top": True,
}
