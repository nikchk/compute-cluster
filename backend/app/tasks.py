"""Власне обчислення: розв'язування СЛАР методом Гауса. Виконується в окремому ОС-процесі
(його можна примусово вбити при скасуванні/перевищенні часу).
Прогрес передається батьківському процесу через Pipe."""
import time


def linear_system(p, report):
    import numpy as np
    n = p["n"]
    t0 = time.time()
    rng = np.random.default_rng(p["seed"])
    report(0, "Генерація системи")
    A0 = rng.uniform(-1, 1, (n, n))
    b0 = rng.uniform(-1, 1, n)
    Ab = np.hstack([A0, b0[:, None]])
    step = max(1, n // 100)
    for k in range(n - 1):
        piv = k + int(np.argmax(np.abs(Ab[k:, k])))
        if abs(Ab[piv, k]) < 1e-14:
            raise ValueError("Матриця вироджена")
        if piv != k:
            Ab[[k, piv]] = Ab[[piv, k]]
        factors = Ab[k + 1:, k] / Ab[k, k]
        Ab[k + 1:, k:] -= np.outer(factors, Ab[k, k:])
        if k % step == 0:
            report(85.0 * k / n, f"Прямий хід: стовпець {k}/{n}")
    x = np.zeros(n)
    for i in range(n - 1, -1, -1):
        x[i] = (Ab[i, n] - Ab[i, i + 1:n] @ x[i + 1:]) / Ab[i, i]
        if i % step == 0:
            report(85.0 + 13.0 * (n - i) / n, "Зворотний хід")
    report(98, "Перевірка нев'язки")
    resid = float(np.linalg.norm(A0 @ x - b0) / np.linalg.norm(b0))
    return {"n": n, "relative_residual": resid, "x_head": [float(v) for v in x[:8]],
            "x_norm": float(np.linalg.norm(x)), "elapsed_s": round(time.time() - t0, 2)}


HANDLERS = {"linear_system": linear_system}


def child_main(task_type, params, conn):
    def report(pct, stage):
        conn.send(("progress", float(pct), stage))
    try:
        result = HANDLERS[task_type](params, report)
        conn.send(("result", result))
    except MemoryError:
        conn.send(("error", "Не вистачило пам'яті"))
    except Exception as e:  # noqa
        conn.send(("error", f"{type(e).__name__}: {e}"))
    finally:
        conn.close()
