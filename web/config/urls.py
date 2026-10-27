from django.contrib import admin
from django.contrib.staticfiles.views import serve
from django.urls import include, path, re_path

urlpatterns = [
    re_path(r"^static/(?P<path>.*)$", serve, {"insecure": True}),
    path("admin/", admin.site.urls),
    path("", include("plaza.urls")),
]
