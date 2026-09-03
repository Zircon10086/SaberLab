"""ScoreSaber PP prediction: accuracy -> PP curve for one ranked map difficulty.

Formula replicated from ScoreSaber's official endpoint
(GET https://scoresaber.com/api/v2/realms/1/pp-curve, realm 1, fetched
2026-08-31):

    pp = max_pp * curve(acc)

``curve`` is a piecewise-linear [acc -> multiplier] table normalized at
acc=0.95 (multiplier == 1.0); ``max_pp`` is the leaderboard's maxPP from the
leaderboard info API, i.e. the PP awarded for a 95% acc play. Verified against
the local database: for every replay whose recorded pp belongs to the replay
itself, pp / curve(acc) == max_pp within +-0.1% (see _tmp/verify_pp_curve.py).

SS_CURVE is embedded (frozen) instead of fetched at runtime: this module is
deterministic analysis and must stay offline (local-first). If ScoreSaber
reworks the curve, re-fetch the endpoint and update the table + provenance
note below.

Pure functions only: no network, no LLM, no UI. A normal local-play estimate
uses the replay's deterministic accuracy. For the PP actually awarded to an NF
play, ScoreSaber sees the halved effective score (``ss_accuracy``). The preview
control is a separate concept and remains anchored to the replay's displayed
accuracy so it never jumps to the 60% slider floor.
"""
from __future__ import annotations

# [accuracy, multiplier] ascending; normalized at acc=0.95 -> 1.0.
# Source: scoresaber.com/api/v2/realms/1/pp-curve ("curve"), fetched 2026-08-31.
SS_CURVE: tuple[tuple[float, float], ...] = (
    (0.0, 0.0), (0.6, 0.18223233667439062), (0.65, 0.5866010012767576),
    (0.7, 0.6125565959114954), (0.75, 0.6451808210101443),
    (0.8, 0.6872268862950283), (0.825, 0.7150465663454271),
    (0.85, 0.7462290664143185), (0.875, 0.7816934560296046),
    (0.9, 0.825756123560842), (0.91, 0.8488375988124467),
    (0.92, 0.8728710341448851), (0.93, 0.9039994071865736),
    (0.94, 0.9417362980580238), (0.95, 1.0), (0.955, 1.0388633331418984),
    (0.96, 1.0871883573850478), (0.965, 1.1552120359501035),
    (0.97, 1.2485807759957321), (0.9725, 1.3090333065057616),
    (0.975, 1.3807102743105126), (0.9775, 1.4664726399289512),
    (0.98, 1.5702410055532239), (0.9825, 1.697536248647543),
    (0.985, 1.8563887693647105), (0.9875, 2.058947159052738),
    (0.99, 2.324506282149922), (0.99125, 2.4902905794106913),
    (0.9925, 2.685667856592722), (0.99375, 2.9190155639254955),
    (0.995, 3.2022017597337955), (0.99625, 3.5526145337555373),
    (0.9975, 3.996793606763322), (0.99825, 4.325027383589547),
    (0.999, 4.715470646416203), (0.9995, 5.019543595874787),
    (1.0, 5.367394282890631),
)

# Slider range shown in the UI: below 0.6 the curve collapses toward 0
# (the API's [0.6, 0.182] -> [0, 0] segment), which is not meaningful to explore.
SLIDER_LO = 0.60
SLIDER_HI = 1.00


def curve_multiplier(acc: float) -> float:
    """Curve multiplier at `acc` (piecewise-linear over SS_CURVE knots)."""
    if acc is None:
        return 0.0
    if acc <= SS_CURVE[0][0]:
        return SS_CURVE[0][1]
    if acc >= SS_CURVE[-1][0]:
        return SS_CURVE[-1][1]
    for (a0, m0), (a1, m1) in zip(SS_CURVE, SS_CURVE[1:]):
        if a0 <= acc <= a1:
            if a1 == a0:
                return m0
            return m0 + (m1 - m0) * (acc - a0) / (a1 - a0)
    return 0.0


def predict_pp(max_pp: float, acc: float) -> float:
    """PP predicted for one play: max_pp (the 95%-acc award) scaled by curve(acc)."""
    return (max_pp or 0.0) * curve_multiplier(acc)


def preview_payload(max_pp: float, default_acc: float | None = None) -> dict:
    """Payload for the accuracy-preview card (frontend interpolates the knots).

    curve knots are the native SS_CURVE accs with pp values substituted in, so
    the piecewise-linear shape is preserved exactly and the payload stays tiny.
    """
    # Failed/exit replays can legitimately show less than 60%. Extend the
    # exploration range to that replay instead of snapping its control to 60%.
    lo = (min(SLIDER_LO, max(0.0, float(default_acc)))
          if default_acc is not None else SLIDER_LO)
    return {
        "lo": lo,
        "hi": SLIDER_HI,
        "max_pp": max_pp,
        "default_acc": default_acc,
        "curve": [[a, (max_pp or 0.0) * m] for a, m in SS_CURVE],
    }


def ss_accuracy(accuracy: float | None, score: int | None,
                score_effective: int | None) -> float | None:
    """Accuracy ScoreSaber uses when awarding PP for this replay."""
    if accuracy is None:
        return None
    if score and score_effective is not None and score_effective != score:
        return accuracy * (score_effective / score)
    return accuracy
