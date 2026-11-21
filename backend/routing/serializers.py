from datetime import timezone

from rest_framework import serializers

INTERVAL_CHOICES = (10, 25, 50)


class TripRequestSerializer(serializers.Serializer):
    origin = serializers.CharField(max_length=200, trim_whitespace=True)
    destination = serializers.CharField(max_length=200, trim_whitespace=True)
    departure = serializers.DateTimeField(help_text="ISO 8601 with UTC offset, e.g. 2026-11-21T08:00:00-06:00")
    load_lb = serializers.FloatField(min_value=0, max_value=200_000)
    interval_mi = serializers.ChoiceField(choices=INTERVAL_CHOICES, default=25)
    # Last-resort path: Open-Meteo data the browser fetched itself (validated in weather.py).
    client_weather = serializers.JSONField(required=False)

    def validate_departure(self, value):
        if value.tzinfo is None:  # treat naive times as UTC rather than guessing a zone
            value = value.replace(tzinfo=timezone.utc)
        return value

    def validate(self, attrs):
        if attrs["origin"].lower() == attrs["destination"].lower():
            raise serializers.ValidationError("Origin and destination must be different.")
        return attrs
