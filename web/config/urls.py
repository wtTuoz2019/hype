from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.static import serve

urlpatterns = [
    re_path(
        r"^static/(?P<path>.*)$",
        serve,
        {"document_root": settings.HYPE_ROOT / "alphascope-simple"},
    ),
    path("admin/", admin.site.urls),
    path("", include("plaza.urls")),
]
