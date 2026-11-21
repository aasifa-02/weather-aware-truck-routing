import logging

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .serializers import TripRequestSerializer
from .services.heatmap import HeatmapError, build_heatmap
from .services.planner import PlanError, TripRequest, plan_trip
from .services.weather import WeatherUnavailable

logger = logging.getLogger(__name__)


@api_view(["GET"])
def health(request):
    return Response({"status": "ok"})


@api_view(["POST"])
def plan(request):
    ser = TripRequestSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    d = ser.validated_data
    try:
        result = plan_trip(
            TripRequest(
                origin=d["origin"],
                destination=d["destination"],
                departure=d["departure"],
                load_lb=d["load_lb"],
                interval_mi=int(d["interval_mi"]),
                client_weather=d.get("client_weather"),
            )
        )
    except WeatherUnavailable as exc:
        return _weather_unavailable(exc)
    except PlanError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
    except Exception:  # pragma: no cover - last resort so the UI gets JSON, not HTML
        logger.exception("Trip planning failed")
        return Response({"detail": "Unexpected error while planning the trip."}, status=500)
    return Response(result)


@api_view(["GET", "POST"])
def heatmap(request, plan_id: str):
    """GET builds the heatmap server-side; POST {client_weather} scores browser-fetched data."""
    client_weather = None
    if request.method == "POST":
        client_weather = (request.data or {}).get("client_weather")
        if client_weather is None:
            return Response({"detail": "client_weather is required."}, status=status.HTTP_400_BAD_REQUEST)
    try:
        return Response(build_heatmap(plan_id, client_weather))
    except WeatherUnavailable as exc:
        return _weather_unavailable(exc)
    except HeatmapError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_404_NOT_FOUND)


def _weather_unavailable(exc: WeatherUnavailable) -> Response:
    """503 telling the frontend exactly which Open-Meteo request to make from the browser."""
    logger.warning("Server-side weather unavailable: %s", exc)
    return Response(
        {"detail": str(exc), "code": "weather_unavailable", "weather_request": exc.request},
        status=status.HTTP_503_SERVICE_UNAVAILABLE,
    )
