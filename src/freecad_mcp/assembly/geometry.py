"""Small dependency-free rigid alignment solver. Units: mm; vectors: world space."""
import math
from typing import Any, Sequence

Vector = Sequence[float]
Matrix = Sequence[Vector]


def vec(value: Vector) -> tuple[float, ...]:
    if len(value) != 3 or not all(math.isfinite(float(x)) for x in value):
        raise ValueError("Expected a finite 3D vector")
    return tuple(float(x) for x in value)


def add(a: Vector, b: Vector) -> tuple[float, ...]:
    return tuple(x + y for x, y in zip(a, b))


def sub(a: Vector, b: Vector) -> tuple[float, ...]:
    return tuple(x - y for x, y in zip(a, b))


def scale(a: Vector, k: float) -> tuple[float, ...]:
    return tuple(x * k for x in a)


def dot(a: Vector, b: Vector) -> float:
    return sum(x * y for x, y in zip(a, b))


def cross(a: Vector, b: Vector) -> tuple[float, ...]:
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def norm(a: Vector) -> float:
    return math.sqrt(dot(a, a))


def unit(a: Vector) -> tuple[float, ...]:
    length = norm(a)
    if length < 1e-12:
        raise ValueError("Degenerate direction or coincident reference holes")
    return scale(a, 1 / length)


def rotate(matrix: Matrix, point: Vector) -> tuple[float, ...]:
    return tuple(dot(row, point) for row in matrix)


def transform(plan: dict[str, Any], point: Vector) -> tuple[float, ...]:
    return add(rotate(plan["rotation"], point), plan["translation"])


def frame(points: Sequence[Vector], normal: Vector) -> tuple[Vector, Vector, Vector]:
    n = unit(vec(normal))
    delta = sub(vec(points[1]), vec(points[0]))
    x = unit(sub(delta, scale(n, dot(delta, n))))
    return x, cross(n, x), n


def positive(value: float, name: str, allow_zero: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError(name + " must be a finite number")
    v = float(value)
    if not math.isfinite(v) or (v < 0 if allow_zero else v <= 0):
        raise ValueError(name + " must be " + ("nonnegative" if allow_zero else "positive"))
    return v


def solve(fixed: Sequence[Vector], moving: Sequence[Vector], fixed_normal: Vector,
          moving_normal: Vector, gap_mm: float = 0, tolerance_mm: float = 0.01) -> dict[str, Any]:
    """Match explicitly ordered hole intersections and opposing mounting normals.

    Two holes fix clocking; every additional hole must fit the same rigid motion.
    Never guesses correspondences in symmetric patterns.
    """
    tolerance = positive(tolerance_mm, "tolerance_mm")
    gap = positive(gap_mm, "gap_mm", allow_zero=True)
    if len(fixed) != len(moving) or len(fixed) < 2:
        raise ValueError("Supply the same number of ordered holes, at least two per interface")
    fixed, moving = [vec(p) for p in fixed], [vec(p) for p in moving]
    n = unit(vec(fixed_normal))
    target = [add(p, scale(n, gap)) for p in fixed]
    a, b = frame(moving, moving_normal), frame(target, scale(n, -1))
    rotation = tuple(tuple(sum(b[k][i]*a[k][j] for k in range(3)) for j in range(3)) for i in range(3))
    result = {"rotation": rotation, "translation": sub(target[0], rotate(rotation, moving[0]))}
    residuals = [norm(sub(transform(result, p), q)) for p, q in zip(moving, target)]
    result.update(residuals_mm=residuals, max_error_mm=max(residuals), gap_mm=gap, tolerance_mm=tolerance)
    if max(residuals) > tolerance:
        raise ValueError("Hole pattern cannot be aligned rigidly within tolerance: max error %.6g mm" % max(residuals))
    return result
