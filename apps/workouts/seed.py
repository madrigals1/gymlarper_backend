"""Starter exercise catalogue offered on first run.

Deliberately small and machine-centric: this app is for logging what a specific
station at a specific gym felt like, not for browsing 900 movements.
"""

SEED_EXERCISES: list[dict] = [
    # Legs
    {"name": "Back Squat", "muscle_group": "quads", "equipment": "barbell", "is_machine": False},
    {"name": "Leg Press", "muscle_group": "quads", "equipment": "machine", "is_machine": True},
    {"name": "Leg Extension", "muscle_group": "quads", "equipment": "machine", "is_machine": True},
    {
        "name": "Lying Leg Curl",
        "muscle_group": "hamstrings",
        "equipment": "machine",
        "is_machine": True,
    },
    {
        "name": "Seated Leg Curl",
        "muscle_group": "hamstrings",
        "equipment": "machine",
        "is_machine": True,
    },
    {"name": "Hip Thrust", "muscle_group": "glutes", "equipment": "barbell", "is_machine": False},
    {
        "name": "Standing Calf Raise",
        "muscle_group": "calves",
        "equipment": "machine",
        "is_machine": True,
    },
    # Push
    {"name": "Bench Press", "muscle_group": "chest", "equipment": "barbell", "is_machine": False},
    {
        "name": "Incline Bench Press",
        "muscle_group": "chest",
        "equipment": "barbell",
        "is_machine": False,
    },
    {"name": "Chest Press", "muscle_group": "chest", "equipment": "machine", "is_machine": True},
    {"name": "Cable Fly", "muscle_group": "chest", "equipment": "cable", "is_machine": True},
    {
        "name": "Overhead Press",
        "muscle_group": "shoulders",
        "equipment": "barbell",
        "is_machine": False,
    },
    {
        "name": "Lateral Raise",
        "muscle_group": "shoulders",
        "equipment": "dumbbell",
        "is_machine": False,
    },
    {
        "name": "Cable Lateral Raise",
        "muscle_group": "shoulders",
        "equipment": "cable",
        "is_machine": True,
    },
    {
        "name": "Triceps Pushdown",
        "muscle_group": "triceps",
        "equipment": "cable",
        "is_machine": True,
    },
    {
        "name": "Overhead Cable Extension",
        "muscle_group": "triceps",
        "equipment": "cable",
        "is_machine": True,
    },
    # Pull
    {"name": "Deadlift", "muscle_group": "back", "equipment": "barbell", "is_machine": False},
    {"name": "Pull Up", "muscle_group": "back", "equipment": "bodyweight", "is_machine": False},
    {"name": "Lat Pulldown", "muscle_group": "back", "equipment": "cable", "is_machine": True},
    {"name": "Seated Cable Row", "muscle_group": "back", "equipment": "cable", "is_machine": True},
    {"name": "Barbell Row", "muscle_group": "back", "equipment": "barbell", "is_machine": False},
    {"name": "Face Pull", "muscle_group": "shoulders", "equipment": "cable", "is_machine": True},
    {"name": "Barbell Curl", "muscle_group": "biceps", "equipment": "barbell", "is_machine": False},
    {"name": "Hammer Curl", "muscle_group": "biceps", "equipment": "dumbbell", "is_machine": False},
    {"name": "Preacher Curl", "muscle_group": "biceps", "equipment": "machine", "is_machine": True},
    {"name": "Cable Curl", "muscle_group": "biceps", "equipment": "cable", "is_machine": True},
    # Core / conditioning
    {"name": "Cable Crunch", "muscle_group": "core", "equipment": "cable", "is_machine": True},
    {
        "name": "Hanging Leg Raise",
        "muscle_group": "core",
        "equipment": "bodyweight",
        "is_machine": False,
    },
    {
        "name": "Treadmill Run",
        "muscle_group": "full_body",
        "equipment": "cardio",
        "is_machine": True,
        "metrics": {"distance_m": 5000, "time_s": 1500},
    },
    {
        "name": "Rowing Machine",
        "muscle_group": "full_body",
        "equipment": "cardio",
        "is_machine": True,
        "metrics": {"distance_m": 2000, "time_s": 480},
    },
    {
        "name": "Assault Bike",
        "muscle_group": "full_body",
        "equipment": "cardio",
        "is_machine": True,
        "metrics": {"distance_m": 500, "time_s": 300},
    },
]
