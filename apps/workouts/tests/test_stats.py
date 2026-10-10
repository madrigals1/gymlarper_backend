from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from apps.workouts import units as unit_utils
from apps.workouts.models import (
    Exercise,
    Workout,
    WorkoutExercise,
    WorkoutSet,
)
from apps.workouts.stats import (
    personal_records,
    streaks,
    summary,
    volume_by_muscle,
    weekly_volume,
)


def make_workout(user, *, days_ago=0, finished=True, sets=None, exercise=None, bodyweight=False):
    started = timezone.now() - timedelta(days=days_ago)
    workout = Workout.objects.create(user=user, started_at=started)
    if finished:
        workout.finished_at = started + timedelta(minutes=60)
        workout.save()

    # Exercise names are unique per user, so reuse one across sessions unless
    # the caller explicitly wants a specific movement.
    if exercise is None:
        exercise, _ = Exercise.objects.get_or_create(
            user=user,
            name="Back Squat",
            defaults={"muscle_group": "quads", "equipment": "barbell"},
        )
    item = WorkoutExercise.objects.create(
        user=user,
        workout=workout,
        exercise=exercise,
        exercise_name=exercise.name,
        muscle_group=exercise.muscle_group,
        equipment=exercise.equipment,
    )
    for index, (reps, weight, unit) in enumerate(sets or []):
        WorkoutSet.objects.create(
            user=user,
            workout_exercise=item,
            position=index,
            reps=reps,
            weight=Decimal(weight) if weight is not None else None,
            unit=unit,
            is_completed=True,
            completed_at=started + timedelta(minutes=index),
            is_bodyweight=bodyweight,
        )
    return workout


class UnitConversionTests(TestCase):
    def test_pounds_to_kg(self):
        # to_kg is quantised to the gram, which is well beyond what matters here.
        self.assertAlmostEqual(float(unit_utils.to_kg(100, "lb")), 45.359237, places=2)

    def test_kg_to_pounds(self):
        self.assertEqual(unit_utils.from_kg(Decimal("45.359237"), "lb"), Decimal("100.00"))

    def test_round_trip_lb_is_stable_to_the_cent(self):
        kg = unit_utils.to_kg("225", "lb")
        self.assertEqual(unit_utils.from_kg(kg, "lb"), Decimal("225.00"))

    def test_unknown_unit_falls_back_to_kg(self):
        self.assertEqual(unit_utils.normalise_unit("stone"), "kg")
        self.assertEqual(unit_utils.normalise_unit(None), "kg")

    def test_convert_between_units(self):
        self.assertEqual(unit_utils.convert(100, "lb", "kg"), unit_utils.to_kg(100, "lb"))
        self.assertEqual(unit_utils.convert(100, "kg", "kg"), Decimal("100"))


class VolumeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="ada")

    def test_volume_sums_kg(self):
        make_workout(self.user, sets=[(10, "100", "kg"), (8, "100", "kg")])
        workout = Workout.objects.get()
        self.assertEqual(workout.volume, 1800.0)

    def test_pounds_are_converted_before_summing(self):
        make_workout(self.user, sets=[(10, "225", "lb")])
        workout = Workout.objects.get()
        self.assertAlmostEqual(workout.volume, 1020.58, places=1)

    def test_summary_totals(self):
        make_workout(self.user, sets=[(10, "100", "kg")], days_ago=0)
        make_workout(self.user, sets=[(5, "60", "kg")], days_ago=2)
        result = summary(self.user)
        self.assertEqual(result["totals"]["workouts"], 2)
        self.assertEqual(result["totals"]["sets"], 2)
        self.assertEqual(result["totals"]["volume_kg"], 1300.0)

    def test_warmups_are_excluded_from_totals(self):
        workout = make_workout(self.user, sets=[])
        item = workout.exercises.first()
        WorkoutSet.objects.create(
            user=self.user, workout_exercise=item, reps=10, weight=Decimal("20"),
            unit="kg", set_type="warmup", is_completed=True, completed_at=timezone.now(),
        )
        self.assertEqual(summary(self.user)["totals"]["volume_kg"], 0.0)

    def test_bodyweight_with_no_load_contributes_nothing(self):
        make_workout(self.user, sets=[(20, None, "kg")], bodyweight=True)
        self.assertEqual(summary(self.user)["totals"]["volume_kg"], 0.0)

    def test_incomplete_sets_do_not_count(self):
        workout = make_workout(self.user, sets=[])
        WorkoutSet.objects.create(
            user=self.user, workout_exercise=workout.exercises.first(),
            reps=10, weight=Decimal("100"), unit="kg", is_completed=False,
        )
        self.assertEqual(summary(self.user)["totals"]["volume_kg"], 0.0)


class StreakTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="ada")

    def test_consecutive_days(self):
        for offset in range(0, 4):
            make_workout(self.user, days_ago=offset, sets=[(5, "50", "kg")])
        self.assertEqual(streaks(self.user)["current_day_streak"], 4)

    def test_gap_breaks_the_streak(self):
        for offset in (0, 1, 5):
            make_workout(self.user, days_ago=offset, sets=[(5, "50", "kg")])
        self.assertEqual(streaks(self.user)["current_day_streak"], 2)

    def test_streak_survives_when_today_has_no_session_yet(self):
        for offset in (1, 2, 3):
            make_workout(self.user, days_ago=offset, sets=[(5, "50", "kg")])
        self.assertEqual(streaks(self.user)["current_day_streak"], 3)

    def test_no_workouts_returns_zero(self):
        result = streaks(self.user)
        self.assertEqual(result["current_day_streak"], 0)
        self.assertIsNone(result["last_workout_at"])


class PersonalRecordTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="ada")

    def test_epley_estimate_ranks_the_heaviest_work(self):
        make_workout(self.user, sets=[(5, "100", "kg")], days_ago=10)
        make_workout(self.user, sets=[(10, "80", "kg")], days_ago=1)
        result = personal_records(self.user)
        self.assertEqual(len(result["records"]), 1)
        record = result["records"][0]
        # 100*(1+5/30)=116.7 beats 80*(1+10/30)=106.7
        self.assertAlmostEqual(record["est_1rm_kg"], 116.67, places=1)

    def test_records_are_grouped_per_exercise(self):
        squat = Exercise.objects.create(user=self.user, name="Squat", muscle_group="quads")
        bench = Exercise.objects.create(user=self.user, name="Bench", muscle_group="chest")
        make_workout(self.user, sets=[(5, "100", "kg")], exercise=squat)
        make_workout(self.user, sets=[(5, "60", "kg")], exercise=bench)
        records = personal_records(self.user)["records"]
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["exercise_name"], "Squat")

    def test_unit_conversion_is_applied(self):
        make_workout(self.user, sets=[(5, "225", "lb")])
        result = personal_records(self.user, unit="kg")
        # 225 lb * (1 + 5/30) = 262.5 lb = 119.07 kg
        self.assertAlmostEqual(result["records"][0]["est_1rm_kg"], 119.07, places=1)


class WeeklyVolumeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="ada")

    def test_series_is_dense_even_with_no_workouts(self):
        series = weekly_volume(self.user, weeks=6)
        self.assertEqual(len(series), 6)

    def test_volume_lands_in_the_right_week(self):
        make_workout(self.user, sets=[(10, "100", "kg")], days_ago=0)
        series = weekly_volume(self.user, weeks=4, unit="kg")
        self.assertEqual(series[-1]["volume_kg"], 1000.0)
        self.assertEqual(series[-1]["workouts"], 1)

    def test_lb_rendering_keeps_kg_truth(self):
        make_workout(self.user, sets=[(10, "100", "kg")])
        series = weekly_volume(self.user, weeks=2, unit="lb")
        self.assertEqual(series[-1]["volume_kg"], 1000.0)
        self.assertAlmostEqual(series[-1]["volume"], 2204.62, places=1)

    def test_volume_by_muscle(self):
        squat = Exercise.objects.create(user=self.user, name="Squat", muscle_group="quads")
        bench = Exercise.objects.create(user=self.user, name="Bench", muscle_group="chest")
        make_workout(self.user, sets=[(10, "100", "kg")], exercise=squat)
        make_workout(self.user, sets=[(10, "40", "kg")], exercise=bench)
        rows = volume_by_muscle(self.user)
        self.assertEqual(rows[0]["muscle_group"], "quads")
        self.assertEqual(rows[0]["volume_kg"], 1000.0)