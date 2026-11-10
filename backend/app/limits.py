"""Опис задач та обмеження їх трудомісткості.
Ті самі правила віддаються клієнту (/api/limits) і перевіряються на frontend,
але остаточна перевірка завжди виконується тут, на сервері."""

TASKS = {
    "linear_system": {
        "title": "Розв'язування СЛАР (метод Гауса з вибором головного елемента)",
        "fields": {
            "n": {"label": "Кількість невідомих n", "type": "int", "min": 2, "max": 2500, "default": 1200},
            "seed": {"label": "Seed генератора", "type": "int", "min": 0, "max": 1_000_000_000, "default": 1},
        },
    },
    "fem_poisson": {
        "title": "Метод скінченних елементів: рівняння Пуассона −Δu = f на [0,1]²",
        "fields": {
            "m": {"label": "Вузлів на сторону сітки m", "type": "int", "min": 4, "max": 300, "default": 120},
            "f": {"label": "Права частина f", "type": "float", "min": -100, "max": 100, "default": 1},
        },
    },
    "mandelbrot": {
        "title": "Обробка зображень: множина Мандельброта",
        "fields": {
            "width": {"label": "Ширина, px", "type": "int", "min": 100, "max": 1600, "default": 800},
            "height": {"label": "Висота, px", "type": "int", "min": 100, "max": 1600, "default": 600},
            "max_iter": {"label": "Макс. ітерацій", "type": "int", "min": 50, "max": 5000, "default": 600},
            "cx": {"label": "Центр X", "type": "float", "min": -2.5, "max": 2.5, "default": -0.5},
            "cy": {"label": "Центр Y", "type": "float", "min": -2.5, "max": 2.5, "default": 0},
            "zoom": {"label": "Масштаб", "type": "float", "min": 1, "max": 100000, "default": 1},
        },
        # добуток полів не повинен перевищувати ліміт (оцінка трудомісткості)
        "product": {"fields": ["width", "height", "max_iter"], "max": 400_000_000,
                    "label": "ширина × висота × ітерації"},
    },
}


def validate(task_type: str, params: dict) -> dict:
    """Повертає очищені параметри або кидає ValueError з поясненням."""
    spec = TASKS.get(task_type)
    if spec is None:
        raise ValueError("Невідомий тип задачі")
    if not isinstance(params, dict):
        raise ValueError("Параметри мають бути об'єктом")
    clean = {}
    for name, f in spec["fields"].items():
        raw = params.get(name, f["default"])
        try:
            val = int(raw) if f["type"] == "int" else float(raw)
        except (TypeError, ValueError):
            raise ValueError(f"Поле «{f['label']}» має бути числом")
        if isinstance(raw, float) and f["type"] == "int" and raw != int(raw):
            raise ValueError(f"Поле «{f['label']}» має бути цілим")
        if val != val or not (f["min"] <= val <= f["max"]):
            raise ValueError(f"«{f['label']}»: допустимий діапазон {f['min']}…{f['max']}")
        clean[name] = val
    prod = spec.get("product")
    if prod:
        total = 1
        for name in prod["fields"]:
            total *= clean[name]
        if total > prod["max"]:
            raise ValueError(f"Задача надто трудомістка: {prod['label']} = {total:,} > {prod['max']:,}")
    return clean
