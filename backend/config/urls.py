from django.conf import settings
from django.http import FileResponse, Http404
from django.urls import include, path, re_path


def spa_index(request, *args, **kwargs):
    """Serve the React index.html for client-side routes (production build only)."""
    index = settings.FRONTEND_DIST / "index.html"
    if not index.exists():
        raise Http404("Frontend build not found. Run `npm run build` in frontend/.")
    return FileResponse(open(index, "rb"), content_type="text/html")


urlpatterns = [
    path("api/", include("routing.urls")),
    re_path(r"^(?!api/|static/).*$", spa_index),
]
