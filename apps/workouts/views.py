from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import ensure_profile

from . import stats as stats_module
from . import units as unit_utils
from .models import (
    Exercise,
    GymLocation,
    MachineSetup,
    TemplateItem,
    Workout,
    WorkoutExercise,
    WorkoutSet,
    WorkoutTemplate,
)
from .serializers import (
    ExerciseSerializer,
    GymLocationSerializer,
    MachineSetupSerializer,
    TemplateItemSerializer,
    WorkoutExerciseSerializer,
    WorkoutSerializer,
    WorkoutSetSerializer,
    WorkoutTemplatePreviewSerializer,
    WorkoutTemplateSerializer,
)
from .sync import MAX_PULL_PER_TYPE, MAX_PUSH_OPS, SYNC_TYPES, apply_push, build_pull

#: A single request must not be able to create an unbounded number of sets.
MAX_SETS_PER_EXERCISE = 30


def _safe_int(
    value, default: int, *, minimum: int = 1, maximum: int = MAX_SETS_PER_EXERCISE
) -> int:
    """Coerce request input to a bounded int instead of raising a 500."""
    try:
        parsed = int(value)
    except TypeError, ValueError:
        return default
    return max(minimum, min(parsed, maximum))


def _decimal_or_none(value):
    """Parse a decimal from untrusted input, returning None rather than 500."""
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except ArithmeticError, ValueError, TypeError:
        raise ValidationError({"weight": "Enter a number."})


def _requested_unit(value, fallback: str) -> str:
    """Normalise a client-supplied unit, falling back to the profile's own."""
    if value in (None, ""):
        return fallback
    return unit_utils.normalise_unit(value)


def _owned_location(request, location_id):
    """Resolve a location id to a row the caller owns, or None."""
    if not location_id:
        return None
    return GymLocation.objects.filter(pk=location_id, user=request.user).first()


def _owned(model, user, pk):
    """Fetch by pk *and* user - never trust an id from the client alone."""
    return get_object_or_404(model, pk=pk, user=user, deleted_at__isnull=True)


class OwnedModelViewSet(viewsets.ModelViewSet):
    """Base viewset hiding other athletes' rows and keeping the user FK server-owned."""

    serializer_class = None
    user_field = "user"

    def get_queryset(self):
        return self.serializer_class.Meta.model.objects.filter(
            user=self.request.user, deleted_at__isnull=True
        ).select_related("user")

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def perform_update(self, serializer):
        serializer.save(user=self.request.user)

    def perform_destroy(self, instance):
        """Soft delete so the tombstone propagates to other devices."""
        instance.soft_delete(self.request.user.id)


class GymLocationViewSet(OwnedModelViewSet):
    serializer_class = GymLocationSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.query_params.get("include_archived") != "true":
            queryset = queryset.filter(is_archived=False)
        return queryset.order_by("kind", "name")

    @action(detail=False, methods=["get"])
    def defaults(self, request):
        """Default locations so a new account is not an empty screen."""
        from .models import LocationKind

        created = []
        for name, kind in (
            ("Home gym", LocationKind.HOME),
            ("Work gym", LocationKind.WORK),
        ):
            location, _ = GymLocation.objects.get_or_create(
                user=request.user, name=name, defaults={"kind": kind}
            )
            created.append(location)
        return Response(GymLocationSerializer(created, many=True).data)


class ExerciseViewSet(OwnedModelViewSet):
    serializer_class = ExerciseSerializer
    filterset_fields = ["muscle_group", "equipment", "archived", "is_machine"]

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.query_params.get("include_archived") != "true":
            queryset = queryset.filter(archived=False)
        return queryset.order_by("name")

    @action(detail=False, methods=["post"], url_path="seed")
    def seed(self, request):
        """Bulk-import a starter exercise list on first run."""
        from .seed import SEED_EXERCISES

        created, skipped = 0, 0
        for entry in SEED_EXERCISES:
            _, was_created = Exercise.objects.get_or_create(
                user=request.user,
                name=entry["name"],
                defaults={
                    "muscle_group": entry.get("muscle_group", "other"),
                    "equipment": entry.get("equipment", "machine"),
                    "is_machine": entry.get("is_machine", True),
                    "metrics": entry.get("metrics", {}),
                },
            )
            created += was_created
            skipped += not was_created
        return Response({"created": created, "skipped": skipped})


class MachineSetupViewSet(OwnedModelViewSet):
    serializer_class = MachineSetupSerializer

    def get_queryset(self):
        queryset = super().get_queryset().select_related("exercise", "location")
        params = self.request.query_params
        if "exercise" in params:
            queryset = queryset.filter(exercise_id=params["exercise"])
        if "location" in params:
            queryset = queryset.filter(location_id=params["location"])
        return queryset

    @action(detail=False, methods=["get"])
    def defaults(self, request):
        """Best remembered setup for an exercise at a location, for one-tap prefill."""
        exercise_id = request.query_params.get("exercise")
        if not exercise_id:
            return Response(
                {"detail": "Query parameter 'exercise' is required."}, status=400
            )

        queryset = MachineSetup.objects.filter(
            user=request.user, exercise_id=exercise_id, deleted_at__isnull=True
        ).select_related("location")
        location_id = request.query_params.get("location")
        if location_id:
            exact = (
                queryset.filter(location_id=location_id).order_by("-updated_at").first()
            )
            if exact:
                return Response(MachineSetupSerializer(exact).data)
        fallback = queryset.order_by("-updated_at").first()
        if fallback:
            return Response(MachineSetupSerializer(fallback).data)
        return Response({"machine_name": "", "settings_json": {}})


class WorkoutTemplateViewSet(OwnedModelViewSet):
    serializer_class = WorkoutTemplateSerializer
    filterset_fields = ["archived"]

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.query_params.get("include_archived") != "true":
            queryset = queryset.filter(archived=False)
        return queryset.prefetch_related(
            Prefetch(
                "items",
                queryset=TemplateItem.objects.filter(
                    user=self.request.user, deleted_at__isnull=True
                )
                .select_related("exercise")
                .order_by("position"),
            )
        )

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def instantiate(self, request, pk=None):
        """Start a workout pre-filled from this template.

        Machine name and dial settings are snapshotted here, so editing the
        setup later never rewrites what this session actually used.
        """
        template = _owned(WorkoutTemplate, request.user, pk)
        # The template is identified by the URL, so don't demand it in the body.
        payload = {"template": str(template.pk), **(request.data or {})}
        preview = WorkoutTemplatePreviewSerializer(data=payload)
        preview.is_valid(raise_exception=True)

        location = preview.validated_data.get("location") or template.default_location

        user = request.user
        profile = ensure_profile(user)

        workout = Workout.objects.create(
            user=user,
            template=template,
            location=location,
            name=preview.validated_data.get("name") or template.name,
            unit=template.default_unit or profile.units,
            started_at=preview.validated_data.get("started_at") or timezone.now(),
        )

        setups = {
            setup.exercise_id: setup
            for setup in MachineSetup.objects.filter(
                user=user,
                deleted_at__isnull=True,
                exercise__in=[
                    item.exercise_id
                    for item in template.items.filter(deleted_at__isnull=True)
                ],
            )
        }
        location_scoped = {
            setup.exercise_id: setup
            for setup in setups.values()
            if location is not None and setup.location_id == location.id
        }

        created_exercises = []
        for item in (
            template.items.filter(deleted_at__isnull=True)
            .select_related("exercise")
            .order_by("position")
        ):
            setup = location_scoped.get(item.exercise_id) or setups.get(
                item.exercise_id
            )
            workout_exercise = WorkoutExercise.objects.create(
                user=user,
                workout=workout,
                exercise=item.exercise,
                exercise_name=item.exercise.name,
                muscle_group=item.exercise.muscle_group,
                equipment=item.exercise.equipment,
                machine_name=setup.machine_name if setup else "",
                machine_settings=(setup.settings_json if setup else {}) or {},
                position=item.position,
                target_sets=item.target_sets,
                target_reps_min=item.target_reps_min,
                target_reps_max=item.target_reps_max,
                notes=item.notes,
            )
            created_exercises.append(workout_exercise)

            # Pre-create the working sets so the athlete only types weight.
            for position in range(min(item.target_sets, MAX_SETS_PER_EXERCISE)):
                workout_set = WorkoutSet.objects.create(
                    user=user,
                    workout_exercise=workout_exercise,
                    position=position,
                    reps=item.target_reps_min,
                    weight=item.target_weight,
                    unit=workout.unit,
                )

        return Response(
            WorkoutSerializer(workout, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"], url_path="items/reorder")
    def reorder_items(self, request, pk=None):
        template = _owned(WorkoutTemplate, request.user, pk)
        order = request.data.get("order") or []
        if not isinstance(order, list):
            return Response(
                {"detail": "'order' must be a list of template item ids."}, status=400
            )
        owned = {
            str(item_id)
            for item_id in template.items.filter(deleted_at__isnull=True).values_list(
                "id", flat=True
            )
        }
        order = [str(item_id) for item_id in order]
        moved = []
        for position, item_id in enumerate(order):
            if item_id not in owned:
                return Response(
                    {"detail": f"Item {item_id} is not in this template."}, status=400
                )
            item = TemplateItem.objects.filter(pk=item_id, user=request.user).first()
            if item is None:
                return Response(
                    {"detail": f"Item {item_id} is not in this template."}, status=400
                )
            item.position = position
            item.save()  # save() is what bumps rev/seq so peers see the move
            moved.append(item)
        template.items_version += 1
        template.save(update_fields=["items_version"])
        return Response(WorkoutTemplateSerializer(template).data)


class WorkoutViewSet(OwnedModelViewSet):
    serializer_class = WorkoutSerializer

    def get_queryset(self):
        queryset = super().get_queryset().select_related("location", "template")
        params = self.request.query_params

        if params.get("status") == "active":
            queryset = queryset.filter(finished_at__isnull=True)
        elif params.get("status") == "finished":
            queryset = queryset.filter(finished_at__isnull=False)
        if params.get("location"):
            queryset = queryset.filter(location_id=params["location"])
        if params.get("template"):
            queryset = queryset.filter(template_id=params["template"])
        if params.get("since"):
            queryset = queryset.filter(started_at__gte=params["since"])

        return queryset.prefetch_related(
            Prefetch(
                "exercises",
                queryset=WorkoutExercise.objects.filter(
                    user=self.request.user, deleted_at__isnull=True
                )
                .order_by("position", "created_at")
                .prefetch_related(
                    Prefetch(
                        "sets",
                        queryset=WorkoutSet.objects.filter(
                            user=self.request.user, deleted_at__isnull=True
                        ).order_by("position", "created_at"),
                    )
                ),
            )
        )

    @action(detail=False, methods=["get"])
    def active(self, request):
        """The in-progress session, if any - this is what the app restores on launch."""
        workout = (
            Workout.objects.filter(
                user=request.user, deleted_at__isnull=True, finished_at__isnull=True
            )
            .select_related("location", "template")
            .prefetch_related(
                Prefetch(
                    "exercises",
                    queryset=WorkoutExercise.objects.filter(
                        user=self.request.user, deleted_at__isnull=True
                    )
                    .order_by("position", "created_at")
                    .prefetch_related(
                        Prefetch(
                            "sets",
                            queryset=WorkoutSet.objects.filter(
                                user=self.request.user, deleted_at__isnull=True
                            ).order_by("position", "created_at"),
                        )
                    ),
                )
            )
            .order_by("-started_at")
            .first()
        )
        if workout is None:
            return Response(
                {"detail": "No workout in progress."}, status=status.HTTP_404_NOT_FOUND
            )
        return Response(WorkoutSerializer(workout).data)

    @action(detail=False, methods=["post"])
    @transaction.atomic
    def start(self, request):
        """Begin an empty workout, or launch one from a template."""
        template_id = request.data.get("template")
        if template_id:
            return self.instantiate(request, pk=template_id)

        user = request.user
        profile = ensure_profile(user)
        existing = Workout.objects.filter(
            user=user, deleted_at__isnull=True, finished_at__isnull=True
        ).first()
        if existing:
            # Two Start taps must not produce two sessions.
            return Response(WorkoutSerializer(existing).data, status=status.HTTP_200_OK)

        location = (
            _owned_location(request, request.data.get("location"))
            or profile.default_location
        )

        workout = Workout.objects.create(
            user=user,
            location=location,
            name=str(request.data.get("name", ""))[:120],
            unit=_requested_unit(request.data.get("unit"), profile.units),
            started_at=request.data.get("started_at") or timezone.now(),
        )
        return Response(WorkoutSerializer(workout).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def finish(self, request, pk=None):
        """Stop the workout and stamp the finish time."""
        workout = _owned(Workout, request.user, pk)
        if workout.finished_at is None:
            workout.finished_at = request.data.get("finished_at") or timezone.now()
            if "notes" in request.data:
                workout.notes = request.data.get("notes") or ""
            if "bodyweight" in request.data:
                workout.bodyweight = _decimal_or_none(request.data.get("bodyweight"))
            if "bodyweight_unit" in request.data:
                workout.bodyweight_unit = (
                    request.data.get("bodyweight_unit") or workout.unit
                )
            workout.save()
        return Response(WorkoutSerializer(workout).data)

    @action(detail=True, methods=["post"], url_path="exercises")
    @transaction.atomic
    def add_exercise(self, request, pk=None):
        """Add an exercise to an in-progress workout, pre-filled from memory."""
        workout = _owned(Workout, request.user, pk)
        exercise = request.data.get("exercise")
        exercise_obj = _owned(Exercise, request.user, exercise) if exercise else None

        position = request.data.get("position")
        if position is None:
            position = WorkoutExercise.objects.filter(workout=workout).count()

        machine_name = request.data.get("machine_name")
        machine_settings = request.data.get("machine_settings")
        if machine_name is None or machine_settings is None:
            location_id = (
                (_owned_location(request, request.data.get("location")) or {}).id
                if request.data.get("location")
                else workout.location_id
            )
            setup_qs = MachineSetup.objects.filter(
                user=request.user,
                deleted_at__isnull=True,
                exercise_id=getattr(exercise_obj, "id", None),
            )
            if location_id:
                scoped = (
                    setup_qs.filter(location_id=location_id)
                    .order_by("-updated_at")
                    .first()
                )
                setup = scoped or setup_qs.order_by("-updated_at").first()
            else:
                setup = setup_qs.order_by("-updated_at").first()
            if setup:
                machine_name = (
                    setup.machine_name if machine_name is None else machine_name
                )
                machine_settings = (
                    setup.settings_json
                    if machine_settings is None
                    else machine_settings
                )

        item = (
            TemplateItem.objects.filter(
                user=request.user, deleted_at__isnull=True, exercise=exercise_obj
            )
            .order_by("-updated_at")
            .first()
        )

        workout_exercise = WorkoutExercise.objects.create(
            user=request.user,
            workout=workout,
            exercise=exercise_obj,
            exercise_name=(
                exercise_obj.name
                if exercise_obj
                else request.data.get("exercise_name", "")
            ),
            muscle_group=exercise_obj.muscle_group if exercise_obj else "",
            equipment=exercise_obj.equipment if exercise_obj else "",
            machine_name=machine_name or "",
            machine_settings=machine_settings or {},
            position=position,
            target_sets=request.data.get("target_sets")
            or (item.target_sets if item else None),
            target_reps_min=request.data.get("target_reps_min")
            or (item.target_reps_min if item else None),
            target_reps_max=request.data.get("target_reps_max")
            or (item.target_reps_max if item else None),
        )

        target_sets = request.data.get("target_sets") or (
            item.target_sets if item else 3
        )
        default_reps = request.data.get("target_reps_min") or (
            item.target_reps_min if item else 8
        )
        for index in range(
            min(_safe_int(target_sets, MAX_SETS_PER_EXERCISE), MAX_SETS_PER_EXERCISE)
        ):
            workout_set = WorkoutSet.objects.create(
                user=request.user,
                workout_exercise=workout_exercise,
                position=index,
                reps=default_reps,
                unit=workout.unit,
            )

        return Response(
            WorkoutExerciseSerializer(workout_exercise).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"], url_path="exercises/reorder")
    def reorder_exercises(self, request, pk=None):
        workout = _owned(Workout, request.user, pk)
        order = request.data.get("order") or []
        owned = {
            str(item_id)
            for item_id in WorkoutExercise.objects.filter(
                workout=workout, deleted_at__isnull=True
            ).values_list("id", flat=True)
        }
        order = [str(item_id) for item_id in order]
        for position, item_id in enumerate(order):
            if item_id not in owned:
                return Response(
                    {"detail": f"Exercise {item_id} is not in this workout."},
                    status=400,
                )
            item = WorkoutExercise.objects.filter(pk=item_id, user=request.user).first()
            if item is None:
                return Response(
                    {"detail": f"Exercise {item_id} is not in this workout."},
                    status=400,
                )
            item.position = position
            item.save()
        return Response(status=status.HTTP_204_NO_CONTENT)


class WorkoutExerciseViewSet(viewsets.ModelViewSet):
    serializer_class = WorkoutExerciseSerializer

    def get_queryset(self):
        return WorkoutExercise.objects.filter(
            user=self.request.user, deleted_at__isnull=True
        ).select_related("exercise", "workout")

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def perform_destroy(self, instance):
        instance.soft_delete(self.request.user.id)

    @action(detail=True, methods=["post"])
    def add_set(self, request, pk=None):
        """Append a set, copying weight/reps from the previous one."""
        item = _owned(WorkoutExercise, request.user, pk)
        last = item.sets.filter(deleted_at__isnull=True).order_by("-position").first()
        new_set = WorkoutSet.objects.create(
            user=request.user,
            workout_exercise=item,
            position=(last.position + 1) if last else 0,
            reps=last.reps if last else (item.target_reps_min or 8),
            weight=last.weight if last else None,
            unit=last.unit if last else item.workout.unit,
        )
        return Response(
            WorkoutSetSerializer(new_set).data, status=status.HTTP_201_CREATED
        )


class WorkoutSetViewSet(viewsets.ModelViewSet):
    serializer_class = WorkoutSetSerializer

    def get_queryset(self):
        return WorkoutSet.objects.filter(
            user=self.request.user, deleted_at__isnull=True
        ).select_related("workout_exercise")

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def perform_destroy(self, instance):
        instance.soft_delete(self.request.user.id)

    @action(detail=True, methods=["post"], url_path="complete")
    def complete(self, request, pk=None):
        """Tick a set off - the single most frequent action in the app."""
        item = _owned(WorkoutSet, request.user, pk)
        done = request.data.get("is_completed", True)
        item.is_completed = bool(done)
        item.completed_at = timezone.now() if item.is_completed else None
        if "reps" in request.data:
            item.reps = _safe_int(
                request.data["reps"], item.reps, minimum=0, maximum=1000
            )
        if "weight" in request.data:
            item.weight = _decimal_or_none(request.data.get("weight"))
        if "rpe" in request.data:
            rpe = _decimal_or_none(request.data.get("rpe"))
            if rpe is not None and not (1 <= rpe <= 10):
                raise ValidationError({"rpe": "RPE must be between 1 and 10."})
            item.rpe = rpe
        return Response(WorkoutSetSerializer(item).data)


# --- Sync --------------------------------------------------------------------


class SyncPushView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_scope = "sync_push"

    def post(self, request):
        ops = request.data.get("ops")
        if not isinstance(ops, list):
            return Response(
                {
                    "error": {
                        "code": "invalid_payload",
                        "message": "'ops' must be a list.",
                        "details": {},
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(ops) > MAX_PUSH_OPS:
            return Response(
                {
                    "error": {
                        "code": "batch_too_large",
                        "message": f"Send at most {MAX_PUSH_OPS} operations per batch.",
                        "details": {},
                    }
                },
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )
        results = apply_push(request.user, ops)
        applied = sum(1 for item in results if item["status"] == "applied")
        conflicts = sum(1 for item in results if item["status"] == "conflict")
        return Response(
            {
                "results": results,
                "applied": applied,
                "conflicts": conflicts,
                "server_time": timezone.now(),
            }
        )


class SyncPullView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            since = int(request.query_params.get("since", 0))
        except TypeError, ValueError:
            return Response(
                {
                    "error": {
                        "code": "invalid_cursor",
                        "message": "'since' must be an integer.",
                        "details": {},
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            limit = int(request.query_params.get("limit", MAX_PULL_PER_TYPE))
        except TypeError, ValueError:
            limit = MAX_PULL_PER_TYPE

        payload = build_pull(request.user, since, limit)
        payload["server_time"] = timezone.now()
        payload["types"] = sorted(SYNC_TYPES.keys())
        return Response(payload)


# --- Dashboard ---------------------------------------------------------------


def _unit(request) -> str:
    from . import units as unit_utils

    return unit_utils.normalise_unit(
        request.query_params.get("unit") or ensure_profile(request.user).units
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def dashboard_summary(request):
    days = _safe_int(
        request.query_params.get("days", 365), 365, minimum=1, maximum=3650
    )
    return Response(
        stats_module.summary(request.user, lookback_days=max(1, min(days, 3650)))
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def weekly_volume(request):
    weeks = _safe_int(request.query_params.get("weeks", 12), 12, minimum=1, maximum=104)
    return Response(
        {
            "unit": _unit(request),
            "series": stats_module.weekly_volume(
                request.user, weeks=max(1, min(weeks, 104)), unit=_unit(request)
            ),
        }
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def personal_records(request):
    limit = _safe_int(request.query_params.get("limit", 50), 50, minimum=1, maximum=500)
    return Response(
        stats_module.personal_records(
            request.user, limit=max(1, min(limit, 500)), unit=_unit(request)
        )
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def volume_by_muscle(request):
    days = _safe_int(request.query_params.get("days", 90), 90, minimum=1, maximum=3650)
    return Response(
        {
            "unit": _unit(request),
            "rows": stats_module.volume_by_muscle(
                request.user, days=max(1, min(days, 3650)), unit=_unit(request)
            ),
        }
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def exercise_progress(request, exercise_id):
    return Response(
        stats_module.exercise_progress(request.user, exercise_id, unit=_unit(request))
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def machines(request):
    return Response({"rows": stats_module.most_used_machines(request.user)})
