"""Власне обчислення. Виконується в окремому ОС-процесі (можна примусово вбити
при скасуванні/перевищенні часу). Прогрес передається батьківському процесу через Pipe."""
import base64
import io
import math
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


def fem_poisson(p, report):
    import numpy as np
    import scipy.sparse as sp
    from scipy.sparse.linalg import cg
    t0 = time.time()
    m, f = p["m"], p["f"]
    N = m * m
    report(2, "Побудова трикутної сітки")
    xs = np.linspace(0.0, 1.0, m)
    X, Y = np.meshgrid(xs, xs)
    coords = np.column_stack([X.ravel(), Y.ravel()])
    idx = np.arange(N).reshape(m, m)
    a, b = idx[:-1, :-1].ravel(), idx[:-1, 1:].ravel()
    c, d = idx[1:, :-1].ravel(), idx[1:, 1:].ravel()
    E = np.vstack([np.column_stack([a, b, d]), np.column_stack([a, d, c])])  # P1-елементи
    report(8, "Збірка матриці жорсткості")
    x, y = coords[E, 0], coords[E, 1]
    bb = np.stack([y[:, 1] - y[:, 2], y[:, 2] - y[:, 0], y[:, 0] - y[:, 1]], 1)
    cc = np.stack([x[:, 2] - x[:, 1], x[:, 0] - x[:, 2], x[:, 1] - x[:, 0]], 1)
    area = 0.5 * np.abs((x[:, 1] - x[:, 0]) * (y[:, 2] - y[:, 0]) - (x[:, 2] - x[:, 0]) * (y[:, 1] - y[:, 0]))
    Ke = (bb[:, :, None] * bb[:, None, :] + cc[:, :, None] * cc[:, None, :]) / (4 * area[:, None, None])
    rows = np.repeat(E, 3, axis=1).ravel()
    cols = np.tile(E, (1, 3)).ravel()
    K = sp.coo_matrix((Ke.ravel(), (rows, cols)), shape=(N, N)).tocsr()
    F = np.bincount(E.ravel(), weights=np.repeat(f * area / 3.0, 3), minlength=N)
    report(18, "Врахування крайових умов")
    mask = np.ones((m, m), bool)
    mask[0, :] = mask[-1, :] = mask[:, 0] = mask[:, -1] = False
    inner = np.flatnonzero(mask.ravel())
    Kii = K[inner][:, inner].tocsr()
    Fi = F[inner]
    r0 = float(np.linalg.norm(Fi)) or 1.0
    tol = 1e-8
    state = {"it": 0}

    def cb(xk):
        state["it"] += 1
        if state["it"] % 10 == 0:
            r = float(np.linalg.norm(Fi - Kii @ xk)) or 1e-300
            frac = min(1.0, max(0.0, math.log(r0 / r) / math.log(1 / tol)))
            report(20 + 75 * frac, f"Метод спряжених градієнтів: ітерація {state['it']}")

    report(20, "Метод спряжених градієнтів")
    sol, info = cg(Kii, Fi, rtol=tol, maxiter=20000, callback=cb)
    if info != 0:
        raise ValueError("CG не збігся")
    report(96, "Постобробка")
    u = np.zeros(N)
    u[inner] = sol
    grid = u.reshape(m, m)
    step = max(1, math.ceil(m / 60))
    small = grid[::step, ::step]
    return {"m": m, "unknowns": int(len(inner)), "elements": int(len(E)), "iterations": state["it"],
            "u_max": float(u.max()), "grid": [[round(float(v), 8) for v in row] for row in small],
            "elapsed_s": round(time.time() - t0, 2)}


def mandelbrot(p, report):
    import numpy as np
    from PIL import Image
    t0 = time.time()
    w, h, mi = p["width"], p["height"], p["max_iter"]
    span_x = 3.0 / p["zoom"]
    span_y = span_x * h / w
    xs = np.linspace(p["cx"] - span_x / 2, p["cx"] + span_x / 2, w)
    ys = np.linspace(p["cy"] - span_y / 2, p["cy"] + span_y / 2, h)
    cnt_all = np.full((h, w), -1, dtype=np.int32)
    rows = max(1, h // 60)
    report(0, "Обчислення множини")
    for r0 in range(0, h, rows):
        c = xs[None, :] + 1j * ys[r0:r0 + rows][:, None]
        z = np.zeros_like(c)
        alive = np.ones(c.shape, bool)
        cnt = np.full(c.shape, -1, dtype=np.int32)
        for i in range(mi):
            z[alive] = z[alive] * z[alive] + c[alive]
            esc = alive & ((z.real ** 2 + z.imag ** 2) > 4.0)
            cnt[esc] = i
            alive &= ~esc
            if not alive.any():
                break
        cnt_all[r0:r0 + rows] = cnt
        report(92.0 * min(h, r0 + rows) / h, f"Обчислено рядків: {min(h, r0 + rows)}/{h}")
    report(93, "Розфарбування")
    t = np.sqrt(np.clip(cnt_all, 0, None) / mi)
    rgb = np.stack([255 * (0.5 + 0.5 * np.sin(6.283 * (3 * t + ph))) for ph in (0.0, 0.33, 0.67)], -1)
    rgb[cnt_all < 0] = 0
    buf = io.BytesIO()
    Image.fromarray(rgb.astype(np.uint8)).save(buf, "PNG")
    report(99, "Кодування PNG")
    return {"width": w, "height": h, "png_base64": base64.b64encode(buf.getvalue()).decode(),
            "elapsed_s": round(time.time() - t0, 2)}


HANDLERS = {"linear_system": linear_system, "fem_poisson": fem_poisson, "mandelbrot": mandelbrot}


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
