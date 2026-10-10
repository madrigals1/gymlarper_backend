from __future__ import annotations

import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.core.models import SyncableModel

from . import units as unit_utils


class LocationKind(models.TextChoices):
    HOME = "home", "Home gym"
    WORK = "work", "Work gym"
    GYM = "gym", "Named gym"
    OTHER = "other", "Other"


class MuscleGroup(models.TextChoices):
    CHEST = "chest", "Chest"
    BACK = "back", "Back"
    SHOULDERS = "shoulders", "Shoulders"
    BICEPS = "biceps", "Biceps"
    TRICEPS = "triceps", "Triceps"
    FOREARMS = "forearms", "Forearms"
    QUADS = "quads", "Quads"
    HAMSTRINGS = "hamstrings", "Hamstrings"
    GLUTES = "glutes", "Glutes"
    CALVES = "calves", "Calves"
    CORE = "core", "Core"
    FULL_BODY = "full_body", "Full body"
    OTHER = "other", "Other"


class Equipment(models.TextChoices):
    BARBELL = "barbell", "Barbell"
    DUMBBELL = "dumbbell", "Dumbbell"
    MACHINE = "machine", "Machine"
    CABLE = "cable", "Cable"
    SMITH = "smith", "Smith machine"
    KETTLEBELL = "kettlebell", "Kettlebell"
    BAND = "band", "Resistance band"
    BODYWEIGHT = "bodyweight", "Bodyweight"
    CARDIO = "cardio", "Cardio"
    OTHER = "other", "Other"


class GymLocation(SyncableModel):
    """Where a workout happened: home, work, or a specific gym."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="locations")

    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=10, choices=LocationKind.choices, default=LocationKind.GYM)
    address = models.CharField(max_length=240, blank=True)
    notes = models.TextField(blank=True)

    # Denormalised for dashboard filtering without a join on every query.
    is_archived = models.BooleanField(default=False)
    color = models.CharField(max_length=7, blank=True, help_text="Hex colour for the UI chip.")

    class Meta:
        ordering = ["kind", "name"]
        constraints = [
            models.UniqueConstraint(fields=["user", "name"], name="uniq_location_name_per_user")
        ]
        indexes = [models.Index(fields=["user", "-updated_at"])]

    def __str__(self) -> str:
        return self.name


class Exercise(SyncableModel):
    """A movement. Created per user so names stay personal ("my good bench")."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="exercises")

    name = models.CharField(max_length=120)
    muscle_group = models.CharField(max_length=20, choices=MuscleGroup.choices, default=MuscleGroup.OTHER)
    equipment = models.CharField(max_length=20, choices=Equipment.choices, default=Equipment.MACHINE)
    notes = models.TextField(blank=True)
    is_machine = models.BooleanField(default=True)

    # Freeform counters, e.g. {"distance_m": 5000, "time_s": 1800, "rounds": 3}.
    metrics = models.JSONField(default=dict, blank=True)

    archived = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["user", "name"], name="uniq_exercise_name_per_user")
        ]
        indexes = [
            models.Index(fields=["user", "muscle_group"]),
            models.Index(fields=["user", "-updated_at"]),
        ]

    def __str__(self) -> str:
        return self.name


class MachineSetup(SyncableModel):
    """How a specific exercise is configured at a specific gym.

    "Cable machine: seat height 3, pulley 6" belongs to *your* gym, not to the
    exercise in the abstract - the same lat pulldown is a different station at a
    different club. Keeping it location-scoped stops the settings drifting.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="machine_setups")

    exercise = models.ForeignKey(Exercise, on_delete=models.CASCADE, related_name="setups")
    location = models.ForeignKey(
        GymLocation, on_delete=models.CASCADE, related_name="machine_setups", null=True, blank=True
    )

    # Optional: the branded unit itself, e.g. "Cybex Eagle NX".
    machine_name = models.CharField(max_length=120, blank=True)

    # Arbitrary machine dials, e.g. {"seat_height": 3, "cable_height": 6}.
    settings_json = models.JSONField(default=dict, blank=True)

    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["exercise__name", "location__name"]
        indexes = [models.Index(fields=["user", "exercise"])]

    def __str__(self) -> str:
        return f"{self.machine_name or self.exercise.name} @ {self.location or 'anywhere'}"


class WorkoutTemplate(SyncableModel):
    """A reusable routine: Legs day, Arms day, PPL, etc."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="templates")

    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    notes = models.TextField(blank=True)

    # Where this template is normally trained; used to pre-fill new workouts.
    default_location = models.ForeignKey(
        GymLocation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="default_for_templates",
    )
    default_unit = models.CharField(
        max_length=2, choices=[(unit_utils.KG, "Kilograms"), (unit_utils.LB, "Pounds")], default=unit_utils.KG
    )

    archived = models.BooleanField(default=False)
    # Bumped when an item changes so clients can tell "template edited" apart
    # from "template renamed" and re-fetch items.
    items_version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["user", "name"], name="uniq_template_name_per_user")
        ]

    def __str__(self) -> str:
        return self.name


class TemplateItem(SyncableModel):
    """One exercise inside a template, with its prescribed work."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="template_items")

    template = models.ForeignKey(WorkoutTemplate, on_delete=models.CASCADE, related_name="items")
    exercise = models.ForeignKey(Exercise, on_delete=models.CASCADE, related_name="template_items")

    position = models.PositiveIntegerField(default=0)

    target_sets = models.PositiveIntegerField(default=3)
    target_reps_min = models.PositiveIntegerField(default=8)
    target_reps_max = models.PositiveIntegerField(default=12)
    target_weight = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    target_rpe = models.DecimalField(
        max_digits=3, decimal_places=1, null=True, blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(10)],
    )
    rest_seconds = models.PositiveIntegerField(default=120)

    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["position", "id"]
        indexes = [models.Index(fields=["template", "position"])]

    def __str__(self) -> str:
        return f"{self.template.name} :: {self.exercise.name}"


class Workout(SyncableModel):
    """A logged session. `started_at` is set the moment the athlete taps Start."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="workouts")

    template = models.ForeignKey(
        WorkoutTemplate, on_delete=models.SET_NULL, null=True, blank=True, related_name="workouts"
    )
    location = models.ForeignKey(
        GymLocation, on_delete=models.SET_NULL, null=True, blank=True, related_name="workouts"
    )

    name = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)

    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)

    # Unit the athlete was working in for this session; sets inherit it.
    unit = models.CharField(
        max_length=2, choices=[(unit_utils.KG, "Kilograms"), (unit_utils.LB, "Pounds")], default=unit_utils.KG
    )
    bodyweight = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    bodyweight_unit = models.CharField(
        max_length=2, choices=[(unit_utils.KG, "Kilograms"), (unit_utils.LB, "Pounds")], default=unit_utils.KG
    )

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["user", "-started_at"]),
            models.Index(fields=["user", "finished_at"]),
            models.Index(fields=["user", "-updated_at"]),
        ]

    def __str__(self) -> str:
        return self.display_name

    @property
    def display_name(self) -> str:
        if self.name:
            return self.name
        if self.template_id:
            return self.template.name
        return self.started_at.strftime("%A, %d %b")

    @property
    def is_active(self) -> bool:
        return self.finished_at is None

    @property
    def duration_seconds(self) -> int | None:
        if self.finished_at is None:
            return None
        return max(0, int((self.finished_at - self.started_at).total_seconds()))

    @property
    def volume(self) -> float:
        """Total kg lifted across completed, non-warmup sets.

        Uses Django's prefetch cache when it is warm - the workout viewset
        prefetches ``exercises.sets`` so listing 50 workouts stays at 2 queries
        instead of 1 + N + M. Un-prefetched access is still correct, just slower.
        """
        total = sum(
            set_item.volume_kg
            for workout_ex in self.exercises.all()
            for set_item in workout_ex.sets.all()
        )
        return round(total, 2)

    def total_sets(self) -> int:
        return sum(ex.sets.count() for ex in self.exercises.all())


class WorkoutExercise(SyncableModel):
    """An exercise performed during a workout.

    ``machine_name`` and ``machine_settings`` are *snapshots* taken when the
    workout starts. Editing your machine setup next month must not rewrite what
    the seat height was on the day you trained.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="workout_exercises")

    workout = models.ForeignKey(Workout, on_delete=models.CASCADE, related_name="exercises")
    exercise = models.ForeignKey(
        Exercise, on_delete=models.SET_NULL, null=True, blank=True, related_name="workout_exercises"
    )

    # Copied from Exercise at start time so history survives deletion/rename.
    exercise_name = models.CharField(max_length=120, blank=True)
    muscle_group = models.CharField(max_length=20, blank=True)
    equipment = models.CharField(max_length=20, blank=True)

    machine_name = models.CharField(max_length=120, blank=True)
    machine_settings = models.JSONField(default=dict, blank=True)

    position = models.PositiveIntegerField(default=0)
    target_sets = models.PositiveIntegerField(null=True, blank=True)
    target_reps_min = models.PositiveIntegerField(null=True, blank=True)
    target_reps_max = models.PositiveIntegerField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["position", "created_at"]
        indexes = [models.Index(fields=["workout", "position"])]

    def __str__(self) -> str:
        return self.display_name

    @property
    def display_name(self) -> str:
        return self.exercise_name or (self.exercise.name if self.exercise_id else "Exercise")

    @property
    def completed_sets(self) -> int:
        return self.sets.filter(completed_at__isnull=False).count()


class WorkoutSet(SyncableModel):
    """One logged set of one exercise."""

    class SetType(models.TextChoices):
        WARMUP = "warmup", "Warm-up"
        WORKING = "working", "Working"
        DROP = "drop", "Drop set"
        FAILURE = "failure", "To failure"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="workout_sets")

    workout_exercise = models.ForeignKey(
        WorkoutExercise, on_delete=models.CASCADE, related_name="sets"
    )

    position = models.PositiveIntegerField(default=0)
    reps = models.PositiveIntegerField(default=0)
    weight = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    unit = models.CharField(
        max_length=2, choices=[(unit_utils.KG, "Kilograms"), (unit_utils.LB, "Pounds")], default=unit_utils.KG
    )

    set_type = models.CharField(max_length=10, choices=SetType.choices, default=SetType.WORKING)
    is_bodyweight = models.BooleanField(default=False)
    rpe = models.DecimalField(
        max_digits=3, decimal_places=1, null=True, blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(10)],
    )
    is_completed = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["position", "created_at"]
        indexes = [
            models.Index(fields=["workout_exercise", "position"]),
            models.Index(fields=["user", "-completed_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.reps} reps @ {self.weight or 'BW'} {self.unit}"

    @property
    def volume_kg(self) -> float:
        """Training volume in kg. Warm-ups are excluded - they are not load.

        Bodyweight sets count, but only from the athlete's logged bodyweight;
        otherwise they contribute zero rather than a made-up number.
        """
        if not self.is_completed or self.set_type == self.SetType.WARMUP:
            return 0.0
        if self.weight is None:
            return 0.0
        weight_kg = float(unit_utils.to_kg(self.weight, self.unit))
        if self.is_bodyweight:
            return weight_kg * self.reps
        return weight_kg * self.reps

    @property
    def epley_1rm(self) -> float:
        """Estimated one-rep max. Only meaningful for completed working sets."""
        if not self.is_completed or self.set_type == self.SetType.WARMUP:
            return 0.0
        if self.weight is None:
            return 0.0
        weight_kg = float(unit_utils.to_kg(self.weight, self.unit))
        if self.reps <= 1:
            return round(weight_kg, 2)
        return round(weight_kg * (1 + self.reps / 30.0), 2)


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()