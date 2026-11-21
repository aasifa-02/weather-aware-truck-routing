"""Django settings for the Weather-Aware Truck Routing API."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).lower() in {"1", "true", "yes", "on"}


SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-insecure-key-change-me")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",") if h.strip()]
CSRF_TRUSTED_ORIGINS = [
    o.strip() for o in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()
]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "routing",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.gzip.GZipMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    }
]

# No models are used; an in-memory SQLite DB keeps Django happy without migrations.
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}

# File-based cache is shared across gunicorn workers and survives restarts,
# so repeated geocoding / routing / weather lookups don't hit upstream APIs.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
        "LOCATION": os.environ.get("CACHE_DIR", str(BASE_DIR / ".cache")),
        "TIMEOUT": 60 * 60 * 6,
        "OPTIONS": {"MAX_ENTRIES": 5000},
    }
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_TZ = True

# The built React app (frontend/dist) is served by WhiteNoise in production,
# so the whole product deploys as a single web service.
FRONTEND_DIST = Path(os.environ.get("FRONTEND_DIST", BASE_DIR.parent / "frontend" / "dist"))
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATIC_ROOT.mkdir(exist_ok=True)
WHITENOISE_ROOT = FRONTEND_DIST if FRONTEND_DIST.exists() else None
WHITENOISE_INDEX_FILE = True

CORS_ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get("CORS_ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
    if o.strip()
]

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": [],
    "UNAUTHENTICATED_USER": None,
    "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.AnonRateThrottle"],
    "DEFAULT_THROTTLE_RATES": {"anon": os.environ.get("API_RATE_LIMIT", "60/min")},
}

# Upstream services (all free, no API keys required).
NOMINATIM_URL = os.environ.get("NOMINATIM_URL", "https://nominatim.openstreetmap.org/search")
PHOTON_URL = os.environ.get("PHOTON_URL", "https://photon.komoot.io/api/")
OSRM_URL = os.environ.get("OSRM_URL", "https://router.project-osrm.org")
OPEN_METEO_URL = os.environ.get("OPEN_METEO_URL", "https://api.open-meteo.com/v1/forecast")
# URL the *browser* uses for the last-resort client-side weather fetch.
OPEN_METEO_PUBLIC_URL = os.environ.get("OPEN_METEO_PUBLIC_URL", "https://api.open-meteo.com/v1/forecast")
MET_NORWAY_URL = os.environ.get("MET_NORWAY_URL", "https://api.met.no/weatherapi/locationforecast/2.0/compact")
OSRM_FALLBACK_URL = os.environ.get("OSRM_FALLBACK_URL", "https://routing.openstreetmap.de/routed-car")
WEATHER_PRIMARY_ENABLED = env_bool("WEATHER_PRIMARY_ENABLED", True)
WEATHER_FALLBACK_ENABLED = env_bool("WEATHER_FALLBACK_ENABLED", True)
# Client-side weather uploads for the heatmap can be ~1-2 MB of JSON.
DATA_UPLOAD_MAX_MEMORY_SIZE = 8 * 1024 * 1024
HTTP_USER_AGENT = os.environ.get(
    "HTTP_USER_AGENT",
    "WeatherAwareTruckRouting/1.0 (+https://github.com/aasifa-02/weather-aware-truck-routing; aasifa@rnatech.in)",
)
HTTP_TIMEOUT = float(os.environ.get("HTTP_TIMEOUT", "20"))

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")},
}

if not DEBUG:
    # Behind a TLS-terminating proxy (Render, Fly, Cloudflare tunnel, ...).
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_CONTENT_TYPE_NOSNIFF = True
    if SECRET_KEY.startswith("dev-only"):
        raise RuntimeError("Set DJANGO_SECRET_KEY when DJANGO_DEBUG is false.")
