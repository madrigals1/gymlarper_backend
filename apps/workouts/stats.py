"""Dashboard aggregates.

Everything here is derived from the athlete's own rows, so the same numbers come
out whether the client computes them locally (offline) or asks the server
(online). Weight maths always normalises through kg via ``units.to_kg``.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Q
from django.utils import timezone

from . import units as unit_utils
from .models import Workout, WorkoutExercise, WorkoutSet

DONE = Q(is_completed=True, workout_exercise__deleted_at__isnull=True)
NOT_WARMUP = ~Q(set_type=WorkoutSet.SetType.WARMUP)


def _week_start(moment):
    """Monday 00:00 of ``moment``'s week.

    Normalising to midnight matters: without it two moments in the same week
    produce different keys (they differ in microseconds) and bucket lookups
    silently miss.
    """
    local = timezone.localtime(moment)
    start_of_day = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_of_day - timedelta(days=start_of_day.weekday())


def _base_sets(user):
    return WorkoutSet.objects.filter(
        user=user,
        deleted_at__isnull=True,
        workout_exercise__deleted_at__isnull=True,
        workout_exercise__workout__deleted_at__isnull=True,
    )


def summary(user, *, lookback_days: int = 365) -> dict:
    """Headline numbers for the dashboard home screen."""
    now = timezone.now()
    since = now - timedelta(days=lookback_days)

    workouts = Workout.objects.filter(
        user=user, deleted_at__isnull=True, started_at__gte=since
    )
    finished = workouts.filter(finished_at__isnull=False)

    total_workouts = finished.count()
    total_sets = _base_sets(user).filter(DONE, NOT_WARMUP).count()

    volume_kg = 0.0
    for weight, unit, reps, is_bodyweight in (
        _base_sets(user)
        .filter(DONE, NOT_WARMUP)
        .values_list("weight", "unit", "reps", "is_bodyweight")
    ):
        if weight is None:
            continue
        volume_kg += float(unit_utils.to_kg(weight, unit)) * reps

    this_week = _week_start(now)
    workouts_this_week = finished.filter(started_at__gte=this_week).count()
    volume_this_week = _volume_between(user, this_week, None)

    return {
        "generated_at": now,
        "lookback_days": lookback_days,
        "totals": {
            "workouts": total_workouts,
            "sets": total_sets,
            "volume_kg": round(volume_kg, 1),
            "training_minutes": _total_minutes(finished),
        },
        "this_week": {
            "workouts": workouts_this_week,
            "volume_kg": round(volume_this_week, 1),
        },
        "streaks": streaks(user),
        "recent_prs": personal_records(user, limit=5)["records"],
    }


def _volume_between(user, start, end) -> float:
    queryset = (
        _base_sets(user)
        .filter(DONE, NOT_WARMUP)
        .filter(workout_exercise__workout__started_at__gte=start)
    )
    if end is not None:
        queryset = queryset.filter(workout_exercise__workout__started_at__lt=end)

    total = 0.0
    for weight, unit, reps in queryset.values_list("weight", "unit", "reps"):
        if weight is None:
            continue
        total += float(unit_utils.to_kg(weight, unit)) * reps
    return total


def _total_minutes(finished_queryset) -> int:
    total = 0
    for started, finished_at in finished_queryset.values_list(
        "started_at", "finished_at"
    ):
        if started and finished_at:
            total += int((finished_at - started).total_seconds())
    return round(total / 60)


def streaks(user) -> dict:
    """Consecutive days and consecutive weeks containing at least one workout."""
    days = list(
        Workout.objects.filter(
            user=user, deleted_at__isnull=True, finished_at__isnull=False
        )
        .order_by("-started_at")
        .values_list("started_at", flat=True)
    )
    if not days:
        return {
            "current_day_streak": 0,
            "longest_day_streak": 0,
            "current_week_streak": 0,
            "last_workout_at": None,
        }

    day_keys = []
    for moment in days:
        key = timezone.localtime(moment).date()
        if key not in day_keys:
            day_keys.append(key)

    today = timezone.localtime(timezone.now()).date()
    day_set = set(day_keys)

    # A streak survives if the athlete trained today *or* yesterday - nobody has
    # finished today's session at 00:05.
    current = 0
    anchor = today if today in day_set else today - timedelta(days=1)
    while anchor in day_set:
        current += 1
        anchor -= timedelta(days=1)

    longest, run = 0, 0
    previous = None
    for key in sorted(day_set):
        run = (
            run + 1
            if previous is not None and key - previous == timedelta(days=1)
            else 1
        )
        longest = max(longest, run)
        previous = key

    weeks = sorted(
        {_week_start(timezone.localtime(moment)).date() for moment in days},
        reverse=True,
    )
    week_streak = 0
    expected = _week_start(timezone.now()).date()
    while expected in weeks:
        week_streak += 1
        expected -= timedelta(days=7)

    return {
        "current_day_streak": current,
        "longest_day_streak": longest,
        "current_week_streak": week_streak,
        "last_workout_at": timezone.localtime(days[0]),
    }


def weekly_volume(user, *, weeks: int = 12, unit: str = "kg") -> list[dict]:
    """Volume per ISO week, oldest first - the main dashboard chart."""
    now = timezone.now()
    start = _week_start(now) - timedelta(weeks=weeks - 1)

    buckets: dict = defaultdict(float)
    sessions: dict = defaultdict(int)
    for started, weight, w_unit, reps in (
        _base_sets(user)
        .filter(DONE, NOT_WARMUP, workout_exercise__workout__started_at__gte=start)
        .values_list("workout_exercise__workout__started_at", "weight", "unit", "reps")
    ):
        if weight is None:
            continue
        bucket = _week_start(started)
        buckets[bucket] += float(unit_utils.to_kg(weight, w_unit)) * reps

    for started in Workout.objects.filter(
        user=user,
        deleted_at__isnull=True,
        finished_at__isnull=False,
        started_at__gte=start,
    ).values_list("started_at", flat=True):
        sessions[_week_start(started)] += 1

    target_unit = unit_utils.normalise_unit(unit)
    series = []
    for offset in range(weeks):
        week = start + timedelta(weeks=offset)
        kg = buckets.get(week, 0.0)
        series.append(
            {
                "week_start": week,
                "volume": float(unit_utils.from_kg(kg, target_unit)),
                "volume_kg": round(kg, 1),
                "workouts": sessions.get(week, 0),
            }
        )
    return series


def personal_records(user, *, limit: int = 50, unit: str = "kg") -> dict:
    """Best estimated 1RM and best single-set weight per exercise."""
    target_unit = unit_utils.normalise_unit(unit)
    best: dict = {}
    for (
        exercise_id,
        exercise_name,
        muscle_group,
        weight,
        w_unit,
        reps,
        completed_at,
    ) in (
        _base_sets(user)
        .filter(DONE, NOT_WARMUP, weight__isnull=False)
        .filter(workout_exercise__exercise__isnull=False)
        .values_list(
            "workout_exercise__exercise_id",
            "workout_exercise__exercise_name",
            "workout_exercise__muscle_group",
            "weight",
            "unit",
            "reps",
            "completed_at",
        )
    ):
        weight_kg = unit_utils.to_kg(weight, w_unit)
        epley = float(weight_kg) if reps <= 1 else float(weight_kg) * (1 + reps / 30.0)

        entry = best.get(exercise_id)
        if entry is None or epley > entry["est_1rm_kg"]:
            if entry is None:
                best[exercise_id] = {
                    "exercise_id": str(exercise_id),
                    "exercise_name": exercise_name,
                    "muscle_group": muscle_group,
                    "est_1rm_kg": epley,
                    "est_1rm": float(unit_utils.from_kg(epley, target_unit)),
                    "best_weight_kg": float(weight_kg),
                    "best_weight": float(unit_utils.from_kg(weight_kg, target_unit)),
                    "reps": reps,
                    "achieved_at": completed_at,
                    "unit": target_unit,
                }
                continue
            entry["est_1rm_kg"] = epley
            entry["est_1rm"] = float(unit_utils.from_kg(epley, target_unit))
            entry["achieved_at"] = completed_at
            entry["reps"] = reps

        # Heaviest single set is tracked independently of estimated max.
        if entry is not None and float(weight_kg) > entry["best_weight_kg"]:
            entry["best_weight_kg"] = float(weight_kg)
            entry["best_weight"] = float(unit_utils.from_kg(weight_kg, target_unit))

    records = sorted(best.values(), key=lambda item: item["est_1rm_kg"], reverse=True)[
        :limit
    ]
    return {"unit": target_unit, "records": records}


def volume_by_muscle(user, *, days: int = 90, unit: str = "kg") -> list[dict]:
    """Where the volume went, grouped by muscle group."""
    since = timezone.now() - timedelta(days=days)
    target_unit = unit_utils.normalise_unit(unit)
    totals: dict[str, float] = defaultdict(float)

    for weight, w_unit, reps, muscle in (
        _base_sets(user)
        .filter(DONE, NOT_WARMUP, workout_exercise__workout__started_at__gte=since)
        .values_list("weight", "unit", "reps", "workout_exercise__muscle_group")
    ):
        if weight is None:
            continue
        totals[muscle or "other"] += float(unit_utils.to_kg(weight, w_unit)) * reps

    rows = [
        {
            "muscle_group": muscle,
            "volume_kg": round(value, 1),
            "volume": float(unit_utils.from_kg(value, target_unit)),
        }
        for muscle, value in totals.items()
    ]
    rows.sort(key=lambda item: item["volume_kg"], reverse=True)
    return rows


def exercise_progress(user, exercise_id, *, unit: str = "kg", limit: int = 60) -> dict:
    """Every logged set for one exercise, oldest first - powers the progress chart."""
    target_unit = unit_utils.normalise_unit(unit)
    rows = []
    for started, weight, w_unit, reps, completed_at, set_type in (
        _base_sets(user)
        .filter(DONE, workout_exercise__exercise_id=exercise_id)
        .order_by("completed_at")
        .values_list(
            "workout_exercise__workout__started_at",
            "weight",
            "unit",
            "reps",
            "completed_at",
            "set_type",
        )[:limit]
    ):
        weight_kg = (
            unit_utils.to_kg(weight, w_unit) if weight is not None else Decimal("0")
        )
        rows.append(
            {
                "date": started,
                "weight": (
                    float(unit_utils.from_kg(weight_kg, target_unit))
                    if weight is not None
                    else None
                ),
                "weight_kg": float(weight_kg) if weight is not None else None,
                "reps": reps,
                "set_type": set_type,
                "est_1rm": (
                    float(
                        unit_utils.from_kg(
                            weight_kg * (1 + reps / 30.0) if reps > 1 else weight_kg,
                            target_unit,
                        )
                    )
                    if weight is not None
                    else None
                ),
                "completed_at": completed_at,
            }
        )
    return {"exercise_id": str(exercise_id), "unit": target_unit, "sets": rows}


def most_used_machines(user, *, limit: int = 10) -> list[dict]:
    """Which machines this athlete actually trains on."""
    return list(
        WorkoutExercise.objects.filter(
            user=user, deleted_at__isnull=True, machine_name__gt=""
        )
        .exclude(machine_name="")
        .values("machine_name")
        .annotate(times_used=Count("id"))
        .order_by("-times_used")[:limit]
    )
