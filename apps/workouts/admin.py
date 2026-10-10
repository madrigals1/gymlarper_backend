from django.contrib import admin

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


class TemplateItemInline(admin.TabularInline):
    model = TemplateItem
    extra = 0
    fields = (
        "position",
        "exercise",
        "target_sets",
        "target_reps_min",
        "target_reps_max",
        "target_weight",
    )


class WorkoutSetInline(admin.TabularInline):
    model = WorkoutSet
    extra = 0
    fields = ("position", "reps", "weight", "unit", "set_type", "is_completed", "rpe")


class WorkoutExerciseInline(admin.TabularInline):
    model = WorkoutExercise
    extra = 0
    fields = ("position", "exercise", "exercise_name", "machine_name", "machine_settings")
    inlines = [WorkoutSetInline]


@admin.register(GymLocation)
class GymLocationAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "kind", "is_archived")
    list_filter = ("kind", "is_archived")
    search_fields = ("name", "user__email")


@admin.register(Exercise)
class ExerciseAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "muscle_group", "equipment", "archived")
    list_filter = ("muscle_group", "equipment", "archived")
    search_fields = ("name",)


@admin.register(MachineSetup)
class MachineSetupAdmin(admin.ModelAdmin):
    list_display = ("__str__", "user", "machine_name", "location")
    search_fields = ("machine_name", "exercise__name")


@admin.register(WorkoutTemplate)
class WorkoutTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "archived", "items_version")
    search_fields = ("name",)
    inlines = [TemplateItemInline]


@admin.register(Workout)
class WorkoutAdmin(admin.ModelAdmin):
    list_display = ("display_name", "user", "location", "started_at", "finished_at", "unit")
    list_filter = ("unit",)
    search_fields = ("name", "user__email")
    date_hierarchy = "started_at"
    inlines = [WorkoutExerciseInline]
