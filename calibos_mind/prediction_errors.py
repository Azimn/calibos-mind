"""Prediction-error trace — the engine's own surprise, made visible.

Domain 21 (Error and imperfection) audit: the mind already has a real
prediction loop — ``ExpectationTracker`` (``calibos_mind/expectation.py``)
holds a per-key EMA of actual event valence and the ``_own_event`` overlay
feeds it to the frozen engine as ``expected_valence``, so the engine's
surprise becomes ``|actual - ema|`` — a function of lived history. But
that surprise is computed and consumed silently inside the engine:
nothing ever observes a failed prediction, so domain 21 check (10) "No
failed predictions" stays partial and check (15) "Errors have no
downstream consequences" has no visible error to carry consequences.

This module makes the engine's own surprise visible. It mirrors
``counterfactual.py`` exactly in shape and discipline:

- Sidecar ``prediction_errors.json``,
  ``{"records": [...], "prediction_carrying_events": N}``. One record per
  suprathreshold prediction error: ``tick``, ``key``, ``expected``,
  ``actual``, ``error``, ``n``. No store record is ever minted from this
  path — sidecar only, like counterfactual traces. ``n`` is the
  prediction-event ordinal (the 1-based count of prediction-carrying
  owned events so far) and ``prediction_carrying_events`` is the running
  total: both exist so ``mind predictions`` can report the trace rate
  over the last 100 prediction-carrying owned events exactly, from the
  sidecar alone. They carry no other semantics.
- Hook inside the existing ``_own_event_with_expectations`` overlay in
  ``expectation.py``: when a non-explicit event carries a tracker
  prediction (EMA exists for its key), compute ``error = |actual - ema|``
  with the same ``actual`` the overlay already hands the engine
  (``event.actual_valence if not None else event.valence``), using the
  pre-learn EMA captured before the engine body runs. If
  ``error >= THRESHOLD``, append the trace. The observation runs AFTER
  the engine's own body (use-then-update ordering, like ``learn``), so a
  failed event never traces, and it is strictly subordinate to the tick:
  an observation failure is swallowed (counterfactual discipline) and
  can never break a tick the pristine engine would have completed. The
  overlay already writes the expectations sidecar from ``learn()`` inside
  this same call, so a second sidecar write here is precedented, not a
  new write-site class.
- ``mind predictions``: a read-only report command (counts, trace rate
  over the last 100 prediction-carrying owned events, recent traces,
  largest errors). It constructs the tracker directly from the sidecar
  path — no subject, no store touch — so it never writes the sidecar and
  never mints one on a fresh store.
- No change to the frozen engine or the cartridge (fingerprint
  unchanged). Revert = delete this module + the hook lines in
  ``expectation.py`` + the ``_attach_prediction_errors`` wiring in
  ``subject.py`` + the ``cmd_predictions``/path wiring in ``cli.py`` +
  the sidecar, same as the counterfactual precedent.

Threshold calibration (measured 2026-11-14 on synthetic streams with
``ExpectationTracker`` directly, before fixing the constant; script at
/tmp/calibrate_prediction_errors.py, seeds in the CHANGELOG entry).
Valence lives in [-1, 1] (``clamp_signed``), so ``|actual - ema|`` spans
[0, 2]:

- Step stream (20 same-key events at 0.5, then 0.0): errors after the
  step are 0.5, 0.35, 0.245, 0.1715, ... — the EMA tail decays by
  (1 - 0.3) per event. Exactly one trace requires THRESHOLD in
  (0.35, 0.5]: the first error must fire, the second (0.35) must not.
- Mixed stream (6 keys, per-key random-walk valence, start U(-0.5, 0.5),
  step N(0, 0.1), clipped to [-1, 1], 600 events, seed 20261114):
  mean error 0.109, median 0.093, p90 0.224, p95 0.276, p99 0.373,
  max 0.444. Trace rates: 0.40 -> 0.34%, 0.45 -> 0.00%, 0.50 -> 0.00%.
  Seed sweep (5 seeds): at 0.45 the rate stays <= 0.17% at sigma 0.1
  and <= 3.03% at sigma 0.15; at 0.40 one sigma-0.15 seed reaches
  6.57%, over the 5% tripwire. At sigma 0.2 (a key wandering that fast
  is near-noise) even 0.50 exceeds 5% — that regime is what the
  in-vivo revert signal watches, not what the threshold is tuned for.
- Constant stream (100 same-key events at 0.3): every error exactly
  0.0 — zero traces at any threshold > 0.
- IID-uniform reference (6 keys, valence U(-1, 1) per event): mean
  error 0.517, ~54% trace rate at 0.45. Reported, not calibrated
  against: a key whose valence is pure noise carries no predictive
  signal, so its errors SHOULD trace often. The calibration target is
  temporally coherent lived events, not noise.

Chosen THRESHOLD = 0.45: above the mixed stream's p99 (0.373) and the
deterministic step-tail second error (0.35), below the 0.5 step that
must trace, with <5% on coherent streams up to sigma 0.15. A 0.50
threshold was rejected despite a lower rate: it sits exactly on the
fitness step's error, with zero margin. A 0.40 threshold was rejected:
it fails the <5% bar on a sigma-0.15 seed.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

# Calibrated threshold on |actual - ema| (see module docstring): a
# prediction error at or above this mints a trace. Must stay in
# (0.35, 0.5] — the fitness step's first error is 0.5 and its EMA-tail
# second error is 0.35, so the step traces exactly once iff the
# threshold sits strictly between them.
THRESHOLD = 0.45

# Bounded sidecar: only the most recent records are kept. A failed
# prediction is evidence for a falsification pass, not history worth
# keeping forever — and suprathreshold errors are rare (<5% of
# prediction-carrying events by the fitness window).
PREDICTION_ERROR_CAP = 128

# Default window for the trace-rate report: the last 100
# prediction-carrying owned events.
RATE_WINDOW = 100


def _finite_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) \
        and math.isfinite(value)


class PredictionErrorTracker:
    """Bounded local sidecar for suprathreshold prediction errors.

    ``data`` is ``{"records": [...], "prediction_carrying_events": N}``,
    oldest first, capped at PREDICTION_ERROR_CAP. The constructor only
    reads the sidecar; observations move exclusively through
    ``observe()`` on the waking-event path (the ``_own_event`` overlay).
    Read-only commands and dream ticks never write.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.records: list = []
        self.prediction_carrying_events: int = 0
        if self.path.exists():
            loaded = self._load(self.path)
            self.records = loaded["records"]
            self.prediction_carrying_events = loaded["prediction_carrying_events"]

    @staticmethod
    def _load(path: Path) -> dict:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"corrupt prediction-errors sidecar at {path}: {exc}") from exc
        if not isinstance(raw, dict) or not isinstance(raw.get("records"), list):
            raise ValueError(
                f"corrupt prediction-errors sidecar at {path}: "
                "expected {'records': [...], 'prediction_carrying_events': N}"
            )
        if "prediction_carrying_events" not in raw:
            raise ValueError(
                f"corrupt prediction-errors sidecar at {path}: "
                "missing 'prediction_carrying_events' section"
            )
        count = raw["prediction_carrying_events"]
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError(
                f"corrupt prediction-errors sidecar at {path}: "
                "bad 'prediction_carrying_events' value"
            )
        records: list = []
        for rec in raw["records"]:
            if not isinstance(rec, dict):
                raise ValueError(
                    f"corrupt prediction-errors sidecar at {path}: bad record")
            tick, key = rec.get("tick"), rec.get("key")
            expected, actual, error = (rec.get("expected"), rec.get("actual"),
                                       rec.get("error"))
            n = rec.get("n")
            if (isinstance(tick, bool) or not isinstance(tick, int)
                    or not isinstance(key, str)
                    or not _finite_number(expected)
                    or not _finite_number(actual)
                    or not _finite_number(error)
                    or isinstance(n, bool) or not isinstance(n, int)):
                raise ValueError(
                    f"corrupt prediction-errors sidecar at {path}: "
                    f"bad record for {key!r}")
            records.append({
                "tick": tick,
                "key": key,
                "expected": float(expected),
                "actual": float(actual),
                "error": float(error),
                "n": n,
            })
        return {
            "records": records[-PREDICTION_ERROR_CAP:],
            "prediction_carrying_events": count,
        }

    def observe(self, *, tick: int, key: str, expected: float,
                actual: float, error: float) -> bool:
        """Record one prediction-carrying owned event; trace iff suprathreshold.

        Call only for events that carried a tracker prediction (EMA
        existed for the key) — unknown-key first events and explicit
        ``expected_valence`` events never reach here; the overlay owns
        that gating. Increments the prediction-event counter and, when
        ``error >= THRESHOLD``, appends the trace. Saves unconditionally:
        the counter is data the report's rate denominator needs, so a
        below-threshold event still moves the sidecar (same write
        frequency as the expectations ``learn()`` in the same call).
        Returns True iff a trace was minted.
        """
        if isinstance(tick, bool) or not isinstance(tick, int):
            raise ValueError(f"bad tick for prediction-error observe: {tick!r}")
        if not isinstance(key, str):
            raise ValueError(f"bad key for prediction-error observe: {key!r}")
        if not (_finite_number(expected) and _finite_number(actual)
                and _finite_number(error)):
            raise ValueError(
                "non-finite value for prediction-error observe: "
                f"expected={expected!r} actual={actual!r} error={error!r}")
        self.prediction_carrying_events += 1
        traced = False
        if float(error) >= THRESHOLD:
            self.records.append({
                "tick": int(tick),
                "key": str(key),
                "expected": round(float(expected), 4),
                "actual": round(float(actual), 4),
                "error": round(float(error), 4),
                "n": self.prediction_carrying_events,
            })
            self.records = self.records[-PREDICTION_ERROR_CAP:]
            traced = True
        self.save()
        return traced

    def report(self, window: int = RATE_WINDOW) -> dict:
        """Pure-read summary for ``mind predictions``. Never writes.

        The window rate counts traces whose prediction-event ordinal
        falls in the last ``window`` prediction-carrying events. The
        rate is None (reported as "undefined", never a quiet zero) when
        no prediction-carrying event has been observed yet. The window
        is clamped to ``PREDICTION_ERROR_CAP``: the sidecar keeps only
        the newest 128 records, so a wider window could not be
        counted exactly — the cap would evict in-window traces and
        the rate would be silently diluted. The reported ``window``
        is the effective (clamped) window, so the rate is exact for
        the window reported.
        """
        total = self.prediction_carrying_events
        w = min(window, total, PREDICTION_ERROR_CAP)
        if w == 0:
            rate = None
            window_traces = 0
        else:
            window_traces = sum(
                1 for r in self.records if r["n"] > total - w)
            rate = window_traces / w
        return {
            "prediction_carrying_events": total,
            "traces": len(self.records),
            "window": w,
            "window_traces": window_traces,
            "window_rate": rate,
            "recent": self.records[-5:],
            "largest": sorted(self.records, key=lambda r: -r["error"])[:5],
        }

    def reset(self) -> None:
        """Clear the sidecar (used when the store is reseeded).

        Stale failed predictions must never attach to a new
        incarnation's ticks — same bug class as the
        ambivalence/salience resets.
        """
        self.records = []
        self.prediction_carrying_events = 0
        self.save()

    def save(self) -> None:
        # sort_keys makes the bytes deterministic: identical event
        # sequences replay to a byte-identical sidecar (record order is
        # insertion order; sort_keys sorts dict keys, not list items).
        self.path.write_text(
            json.dumps(
                {
                    "records": self.records,
                    "prediction_carrying_events": self.prediction_carrying_events,
                },
                sort_keys=True,
            ) + "\n",
            encoding="utf-8",
        )
