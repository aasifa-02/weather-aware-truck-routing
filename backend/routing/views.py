import logging

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .serializers import TripRequestSerializer
from .services.heatmap import HeatmapError, build_heatmap
from .services.planner import PlanError, TripRequest, plan_trip

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
            )
        )
    except PlanError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
    except Exception:  # pragma: no cover - last resort so the UI gets JSON, not HTML
        logger.exception("Trip planning failed")
        return Response({"detail": "Unexpected error while planning the trip."}, status=500)
    return Response(result)


@api_view(["GET"])
def heatmap(request, plan_id: str):
    try:
        return Response(build_heatmap(plan_id))
    except HeatmapError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_404_NOT_FOUND)
