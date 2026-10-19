"""Minimal Prometheus-compatible metrics registry (Python standard library only).

The registry supports counters (with label sets) and gauges, and renders the
Prometheus text exposition format so the same object can back both:

  * the ``/metrics`` endpoint served by ``app.py`` (scraped by Prometheus), and
  * the ``metrics.csv`` time-series written by ``runner.py``.

No third-party dependency is used (no ``prometheus_client``).
"""

from __future__ import annotations


def _fmt(value: float) -> str:
    """Render a number the way Prometheus would (integers without .0)."""
    if value is None:
        return "NaN"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if f != f:  # NaN
        return "NaN"
    if f.is_integer():
        return str(int(f))
    return repr(round(f, 6))


class Registry:
    """In-memory counters/gauges with deterministic Prometheus rendering."""

    def __init__(self) -> None:
        self._help: dict[str, str] = {}
        self._type: dict[str, str] = {}
        self._counters: dict[str, dict[tuple, float]] = {}
        self._gauges: dict[str, float] = {}

    # -- declaration -------------------------------------------------------
    def declare_counter(self, name: str, help_: str, labelnames=None) -> None:
        self._help[name] = help_
        self._type[name] = "counter"
        self._counters.setdefault(name, {})

    def declare_gauge(self, name: str, help_: str) -> None:
        self._help[name] = help_
        self._type[name] = "gauge"
        self._gauges.setdefault(name, 0.0)

    # -- mutation ----------------------------------------------------------
    def inc(self, name: str, labels=None, value: float = 1.0) -> None:
        key = tuple(sorted((labels or {}).items()))
        bucket = self._counters.setdefault(name, {})
        bucket[key] = bucket.get(key, 0.0) + float(value)

    def set_gauge(self, name: str, value: float) -> None:
        self._gauges[name] = float(value)

    # -- read --------------------------------------------------------------
    def get_counter(self, name: str, labels=None) -> float:
        key = tuple(sorted((labels or {}).items()))
        return self._counters.get(name, {}).get(key, 0.0)

    def get_gauge(self, name: str) -> float:
        return self._gauges.get(name, 0.0)

    def counter_delta(self, name: str, labels, since_key: tuple) -> float:
        """Helper for callers that track previous samples themselves."""
        return self.get_counter(name, labels)

    # -- render ------------------------------------------------------------
    def render(self) -> str:
        lines: list[str] = []
        for name in sorted(self._counters):
            lines.append(f"# HELP {name} {self._help.get(name, '')}")
            lines.append(f"# TYPE {name} counter")
            for key in sorted(self._counters[name]):
                value = self._counters[name][key]
                if key:
                    label = ",".join(f'{k}="{v}"' for k, v in key)
                    lines.append(f"{name}{{{label}}} {_fmt(value)}")
                else:
                    lines.append(f"{name} {_fmt(value)}")
        for name in sorted(self._gauges):
            lines.append(f"# HELP {name} {self._help.get(name, '')}")
            lines.append(f"# TYPE {name} gauge")
            lines.append(f"{name} {_fmt(self._gauges[name])}")
        return "\n".join(lines) + "\n"
