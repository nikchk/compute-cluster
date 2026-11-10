"""Опис задач та обмеження їх трудомісткості.
Ті самі правила віддаються клієнту (/api/limits) і перевіряються на frontend,
але остаточна перевірка завжди виконується тут, на сервері."""

TASKS = {
    "linear_system": {
        "title": "Розв'язування системи лінійних рівнянь (метод Гауса з вибором головного елемента)",
        "fields": {
            "n": {"label": "Кількість невідомих n", "type": "int", "min": 2, "max": 3000, "default": 1500},
            "seed": {"label": "Seed генератора", "type": "int", "min": 0, "max": 1_000_000_000, "default": 1},
        },
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
