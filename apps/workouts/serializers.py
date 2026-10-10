from __future__ import annotations

from decimal import Decimal

from apps.core.serializers import OwnerScopedRelationsMixin
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from . import units as unit_utils
from .models import (
    Equipment,
    Exercise,
    GymLocation,
    LocationKind,
    MachineSetup,
    MuscleGroup,
    TemplateItem,
    Workout,
    WorkoutExercise,
    WorkoutSet,
    WorkoutTemplate,
)


class TimestampMixin(serializers.Serializer):
    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["rev"] = instance.rev
        data["updated_at"] = instance.updated_at
        data["deleted_at"] = instance.deleted_at
        data["client_updated_at"] = instance.updated_at
        return data


# --- Catalog -----------------------------------------------------------------


class UniqueNamePerUserMixin(serializers.Serializer):
    """Turn the per-user name constraint into a 400 instead of a 500.

    The database constraint remains the real guarantee; this just makes the
    common case return something a form can display.
    """

    def _validate_unique_name(self, name: str) -> str:
        request = self.context.get("request")
        if request is None or not getattr(request, "user", None):
            return name
        queryset = self.Meta.model.objects.filter(user=request.user, name=name)
        if self.instance is not None:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise serializers.ValidationError(
                f"You already have a {self.Meta.model._meta.verbose_name} called '{name}'."
            )
        return name


class GymLocationSerializer(UniqueNamePerUserMixin, OwnerScopedRelationsMixin, serializers.ModelSerializer):
    class Meta:
        model = GymLocation
        fields = (
            "id", "rev", "name", "kind", "address", "notes", "is_archived", "color",
            "created_at", "updated_at", "deleted_at",
        )
        read_only_fields = ("id", "rev", "created_at", "updated_at", "deleted_at")

    def validate_kind(self, value):
        valid = {choice for choice, _ in LocationKind.choices}
        if value not in valid:
            raise serializers.ValidationError(f"Unknown location kind '{value}'.")
        return value

    def validate_name(self, value):
        cleaned = value.strip()
        if not cleaned:
            raise serializers.ValidationError("A location needs a name.")
        return self._validate_unique_name(cleaned)


class ExerciseSerializer(UniqueNamePerUserMixin, OwnerScopedRelationsMixin, serializers.ModelSerializer):
    class Meta:
        model = Exercise
        fields = (
            "id", "rev", "name", "muscle_group", "equipment", "notes", "is_machine",
            "metrics", "archived", "created_at", "updated_at", "deleted_at",
        )
        read_only_fields = ("id", "rev", "created_at", "updated_at", "deleted_at")

    def validate_muscle_group(self, value):
        if value not in {c for c, _ in MuscleGroup.choices}:
            raise serializers.ValidationError(f"Unknown muscle group '{value}'.")
        return value

    def validate_equipment(self, value):
        if value not in {c for c, _ in Equipment.choices}:
            raise serializers.ValidationError(f"Unknown equipment '{value}'.")
        return value

    def validate_name(self, value):
        cleaned = value.strip()
        if not cleaned:
            raise serializers.ValidationError("An exercise needs a name.")
        return self._validate_unique_name(cleaned)

    def validate_metrics(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("metrics must be an object.")
        return value


class MachineSetupSerializer(OwnerScopedRelationsMixin, serializers.ModelSerializer):
    exercise_name = serializers.CharField(source="exercise.name", read_only=True)
    location_name = serializers.CharField(source="location.name", read_only=True, default=None)

    class Meta:
        model = MachineSetup
        fields = (
            "id", "rev", "exercise", "exercise_name", "location", "location_name",
            "machine_name", "settings_json", "notes",
            "created_at", "updated_at", "deleted_at",
        )
        read_only_fields = ("id", "rev", "exercise_name", "location_name", "created_at", "updated_at", "deleted_at")

    def validate_settings_json(self, value):
        """Machine dials are a free-form map, but must stay JSON-simple."""
        if value in (None, ""):
            return {}
        if not isinstance(value, dict):
            raise serializers.ValidationError("settings_json must be an object.")
        cleaned = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key.strip():
                raise serializers.ValidationError("Setting keys must be non-empty strings.")
            if isinstance(item, bool) or not isinstance(item, (int, float, str, type(None))):
                raise serializers.ValidationError(
                    f"Setting '{key}' must be a number, string or empty."
                )
            cleaned[key.strip()] = item
        return cleaned

    def validate(self, attrs):
        if self.instance is None and "exercise" not in attrs:
            raise serializers.ValidationError({"exercise": "This field is required."})
        return attrs


# --- Templates ---------------------------------------------------------------


class TemplateItemSerializer(OwnerScopedRelationsMixin, serializers.ModelSerializer):
    exercise_name = serializers.CharField(source="exercise.name", read_only=True)

    class Meta:
        model = TemplateItem
        fields = (
            "id", "rev", "template", "exercise", "exercise_name", "position",
            "target_sets", "target_reps_min", "target_reps_max",
            "target_weight", "target_rpe", "rest_seconds", "notes",
            "created_at", "updated_at", "deleted_at",
        )
        read_only_fields = ("id", "rev", "exercise_name", "created_at", "updated_at", "deleted_at")

    def validate(self, attrs):
        lo = attrs.get("target_reps_min", getattr(self.instance, "target_reps_min", None))
        hi = attrs.get("target_reps_max", getattr(self.instance, "target_reps_max", None))
        if lo is not None and hi is not None and lo > hi:
            raise serializers.ValidationError(
                {"target_reps_min": "Minimum reps cannot exceed the maximum."}
            )
        return attrs


class WorkoutTemplateSerializer(
    UniqueNamePerUserMixin, OwnerScopedRelationsMixin, serializers.ModelSerializer
):
    items = TemplateItemSerializer(many=True, read_only=True)
    item_count = serializers.SerializerMethodField()

    class Meta:
        model = WorkoutTemplate
        fields = (
            "id", "rev", "name", "description", "notes", "default_location",
            "default_unit", "archived", "items_version",
            "items", "item_count", "created_at", "updated_at", "deleted_at",
        )
        read_only_fields = ("id", "rev", "items", "item_count", "items_version", "created_at", "updated_at", "deleted_at")

    def get_item_count(self, obj) -> int:
        return obj.items.filter(deleted_at__isnull=True).count()

    def validate_name(self, value):
        cleaned = value.strip()
        if not cleaned:
            raise serializers.ValidationError("A template needs a name.")
        return self._validate_unique_name(cleaned)

    @property
    def _submitted_items(self):
        """Nested items are read-only on output, so read them off the raw payload."""
        if not isinstance(self.initial_data, dict):
            return None
        items = self.initial_data.get("items")
        return items if isinstance(items, list) else None

    @transaction.atomic
    def create(self, validated_data):
        items = self._submitted_items
        template = WorkoutTemplate.objects.create(**validated_data)
        if items:
            self._write_items(template, items)
        return template

    @transaction.atomic
    def update(self, instance, validated_data):
        items = self._submitted_items
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()

        if items is not None:
            instance.items.filter(deleted_at__isnull=True).update(deleted_at=timezone.now())
            instance.items_version += 1
            instance.save(update_fields=["items_version"])
            self._write_items(instance, items)
        return instance

    def _write_items(self, template, items) -> None:
        for index, raw in enumerate(items):
            payload = dict(raw or {})
            payload.setdefault("template", str(template.pk))
            payload["position"] = payload.get("position", index)
            serializer = TemplateItemSerializer(data=payload, context=self.context)
            serializer.is_valid(raise_exception=True)
            serializer.save(template=template, position=payload["position"], user=template.user)


# --- Workouts ----------------------------------------------------------------


class WorkoutSetSerializer(OwnerScopedRelationsMixin, serializers.ModelSerializer):
    volume_kg = serializers.FloatField(read_only=True)
    epley_1rm = serializers.FloatField(read_only=True)

    class Meta:
        model = WorkoutSet
        fields = (
            "id", "rev", "workout_exercise", "position", "reps", "weight", "unit",
            "set_type", "is_bodyweight", "rpe", "is_completed", "completed_at",
            "notes", "volume_kg", "epley_1rm", "created_at", "updated_at", "deleted_at",
        )
        read_only_fields = ("id", "rev", "volume_kg", "epley_1rm", "created_at", "updated_at", "deleted_at")

    def validate_unit(self, value):
        return unit_utils.normalise_unit(value)

    def validate(self, attrs):
        reps = attrs.get("reps", getattr(self.instance, "reps", 0) if self.instance else 0)
        if reps is None or reps < 0:
            raise serializers.ValidationError({"reps": "Reps cannot be negative."})
        weight = attrs.get("weight", getattr(self.instance, "weight", None) if self.instance else None)
        if weight is not None and weight < 0:
            raise serializers.ValidationError({"weight": "Weight cannot be negative."})
        return attrs


class WorkoutExerciseSerializer(OwnerScopedRelationsMixin, serializers.ModelSerializer):
    display_name = serializers.CharField(read_only=True)
    sets = WorkoutSetSerializer(many=True, read_only=True)
    completed_sets = serializers.IntegerField(read_only=True)

    class Meta:
        model = WorkoutExercise
        fields = (
            "id", "rev", "workout", "exercise", "exercise_name", "display_name",
            "muscle_group", "equipment", "machine_name", "machine_settings",
            "position", "target_sets", "target_reps_min", "target_reps_max",
            "notes", "sets", "completed_sets",
            "created_at", "updated_at", "deleted_at",
        )
        read_only_fields = (
            "id", "rev", "exercise_name", "display_name", "muscle_group", "equipment",
            "sets", "completed_sets", "created_at", "updated_at", "deleted_at",
        )

    def validate_machine_settings(self, value):
        if value in (None, ""):
            return {}
        if not isinstance(value, dict):
            raise serializers.ValidationError("machine_settings must be an object.")
        return value


class WorkoutSerializer(OwnerScopedRelationsMixin, serializers.ModelSerializer):
    display_name = serializers.CharField(read_only=True)
    is_active = serializers.BooleanField(read_only=True)
    duration_seconds = serializers.IntegerField(read_only=True)
    volume = serializers.FloatField(read_only=True)
    location_name = serializers.CharField(source="location.name", read_only=True, default=None)
    template_name = serializers.CharField(source="template.name", read_only=True, default=None)
    exercises = WorkoutExerciseSerializer(many=True, read_only=True)

    class Meta:
        model = Workout
        fields = (
            "id", "rev", "template", "template_name", "location", "location_name",
            "name", "notes", "started_at", "finished_at", "unit",
            "bodyweight", "bodyweight_unit", "display_name", "is_active",
            "duration_seconds", "volume", "exercises",
            "created_at", "updated_at", "deleted_at",
        )
        read_only_fields = (
            "id", "rev", "template_name", "location_name", "display_name", "is_active",
            "duration_seconds", "volume", "exercises", "created_at", "updated_at", "deleted_at",
        )

    def validate_unit(self, value):
        return unit_utils.normalise_unit(value)

    def validate(self, attrs):
        started = attrs.get("started_at", getattr(self.instance, "started_at", None) if self.instance else None)
        finished = attrs.get("finished_at", getattr(self.instance, "finished_at", None) if self.instance else None)
        if started and finished and finished < started:
            raise serializers.ValidationError(
                {"finished_at": "A workout cannot finish before it started."}
            )
        return attrs


class WorkoutTemplatePreviewSerializer(OwnerScopedRelationsMixin, serializers.Serializer):
    """Start a workout from a template without duplicating its items."""

    template = serializers.PrimaryKeyRelatedField(queryset=WorkoutTemplate.objects.all())
    location = serializers.PrimaryKeyRelatedField(
        queryset=GymLocation.objects.all(), required=False, allow_null=True
    )
    name = serializers.CharField(required=False, allow_blank=True, max_length=120)
    started_at = serializers.DateTimeField(required=False)


class ExercisePresetSerializer(serializers.Serializer):
    """Copy a known machine configuration onto a newly started exercise."""

    machine_name = serializers.CharField(required=False, allow_blank=True, max_length=120)
    machine_settings = serializers.DictField(required=False, default=dict)


def decimal_or_none(value) -> Decimal | None:
    return Decimal(value) if value not in (None, "") else None