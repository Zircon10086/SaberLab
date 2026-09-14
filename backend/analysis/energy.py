"""Energy / fail-time simulation (official algorithm port, 2026-09).

WHY THIS MODULE EXISTS
The local .bsor `failTime` field is never written by the BeatLeader mod (498/498
files on this machine are 0.0), so the game's own numbers are unavailable. This
module reproduces the game's algorithm instead, ported line by line from the
decompiled `GameEnergyCounter` (decompiled from `Main.dll` of Beat Saber 1.39.0,
1.40.8 and 1.44.1 — the constants and logic are IDENTICAL across all three, so a
single implementation covers every version; see HANDOFF §4.31 for the evidence).

OFFICIAL RULES (verbatim from the decompiled class)
  constants: kGoodNoteEnergyCharge=0.01  kBadNoteEnergyDrain=0.10
             kMissNoteEnergyDrain=0.15   kHitBombEnergyDrain=0.15
             kGoodBurstSliderElementCharge=0.002
             kBadBurstSliderElementEnergyDrain=0.025
             kMissBurstSliderElementEnergyDrain=0.03
             kObstacleEnergyDrainPerSecond=1.3      (drained per FRAME while the
                                                     head is inside an obstacle:
                                                     ProcessEnergyChange(deltaTime * -1.3))
  start:     Bar mode -> 0.5 ; Battery mode or instaFail -> 1.0 (instaFail = 1 life,
             battery lives = 4, each hit removes 1/lives of the bar)
  cap:       1.0
  fail:      when energy <= 1e-5 -> energy := 0, `gameEnergyDidReach0Event` fires
             (the fail moment). noFail suppresses all changes; a fail is latched so
             later changes are ignored too.
  hands:     the game does not split energy per hand; the split exposed below is
             derived from the note events (which hand made the cut) and is only
             informational.

SCOPE / KNOWN LIMITATIONS (documented, not hidden)
  - Obstacle dwell cannot be integrated exactly: the replay records only the
    moment the head ENTERS an obstacle (WallEvent.time/energy), and not when it
    leaves. The simulation therefore charges ONE frame per entry
    (1.3 * 1/90 s = 0.0144), which matches the smallest observed entries exactly.
    MEASURED ERROR (2026-09, whole local library, 498 files):
      * only 22 replays (4.4%) carry obstacle-entry data at all, 45 entries total;
      * missed drain per entry: median 0.072, mean 0.108, max 0.555 — i.e. up to
        an entire 0.5 Bar, with implied dwell times of 0.056-0.43 s;
      * after re-adding the true drain, 1 replay flips from "never failed" to
        "failed" and 3 more shift their fail time by >1 s (2 by >5 s, worst 17 s).
    So the bias is irrelevant for ~98% of the library and severe for the few
    replays that spend real time inside obstacles. Fixing it needs map obstacle
    geometry + the HMD trajectory (frames are already parsed and carry pose), i.e.
    a head-vs-obstacle sweep — a deliberate follow-up, not an oversight.
  - Burst slider elements are distinguished via `NoteParams.scoring_type` (they do
    occur locally: 1279 in the first 40 replays), so that path is exercised.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

# ---- official constants (decompiled) ----
CHARGE_GOOD = 0.01
DRAIN_BAD = 0.10
DRAIN_MISS = 0.15
DRAIN_BOMB = 0.15
CHARGE_GOOD_BURST = 0.002
DRAIN_BAD_BURST = 0.025
DRAIN_MISS_BURST = 0.03
OBSTACLE_DRAIN_PER_SECOND = 1.3
ENERGY_CAP = 1.0
FAIL_EPSILON = 1e-5
START_BAR = 0.5
START_BATTERY = 1.0
BATTERY_LIVES = 4

# BSOR / NoteData gameplay types (mirror of the game's copy of the same table)
GT_NORMAL = 0
GT_BOMB = 1
GT_BURST_HEAD = 2
GT_BURST_ELEMENT = 3

# NoteParams.scoring_type (decoded from noteID, official NoteParams rule):
# 3 = normal, 4 = slider head, 5 = slider tail, 6 = burst slider head,
# 7 = burst slider element (2/0/1 = no_score/ignore/default, never game-scored).
SCORING_NORMAL = 3
SCORING_BURST_HEAD = 6
SCORING_BURST_ELEMENT = 7

GOOD, BAD, MISS, BOMB = 0, 1, 2, 3

# Every gameplay modifier Beat Saber can carry in the replay's modifier string,
# longest first so "NF" is never matched inside a longer token like "NO".
_MODIFIER_TOKENS = sorted(
    ("BE", "NB", "NF", "NO", "PM", "SC", "GN", "NA", "EZ", "HD", "IN", "OP", "SA",
     "SS", "FS", "SF", "IF", "1L", "360"), key=len, reverse=True)


def parse_modifiers(raw) -> set[str]:
    """Extract gameplay modifiers from `info.modifiers`.

    The field is a comma separated string on every local replay ("NF", "NO,NF"),
    but other writers emit JSON-ish text, so tokens are matched on
    non-alphanumeric boundaries instead of by substring (a naive `"NF" in raw`
    would fire on unrelated substrings).
    """
    if not raw:
        return set()
    text = raw if isinstance(raw, str) else str(raw)
    upper = text.upper()
    parts = {p for p in re.split(r"[^0-9A-Za-z]+", upper) if p}
    return {tok for tok in _MODIFIER_TOKENS if tok in parts}


@dataclass
class EnergyConfig:
    """Gameplay settings that change the energy rules (all from the replay).

    `no_fail_declared` records whether the NF modifier was present, but it does
    NOT suppress the simulation: the game's own counter stops draining once NF is
    active (`if (noFail || _didReachZero) return;` in ProcessEnergyChange) — yet
    NF is auto-enabled at the very moment a run fails, so treating it as "no
    drain" would hide exactly the event we care about (the fail that CAUSED the
    NF). The computed trajectory is therefore the honest one: what the bar did
    while it was still counting down; the NF declaration is reported separately
    so callers can interpret it.
    """
    energy_type: str = "bar"      # bar | battery
    no_fail_declared: bool = False
    insta_fail: bool = False
    obstacle_hit_seconds: float = 1.0 / 90.0   # one frame of dwell per recorded entry
    battery_lives: int = BATTERY_LIVES

    @classmethod
    def from_modifiers(cls, raw, **kw) -> "EnergyConfig":
        mods = parse_modifiers(raw)
        battery = "BE" in mods
        return cls(
            energy_type="battery" if battery else "bar",
            no_fail_declared="NF" in mods,
            insta_fail="IF" in mods,
            **kw,
        )

    @property
    def start_energy(self) -> float:
        if self.insta_fail or self.energy_type == "battery":
            return START_BATTERY
        return START_BAR


@dataclass
class EnergyEvent:
    """One energy change, in game order. Enough to replay the whole run."""
    t: float
    delta: float
    energy: float          # energy AFTER the change
    reason: str            # good | bad | miss | bomb | good_burst | bad_burst |
                           # miss_burst | obstacle | saber_clash | fail
    hand: str = ""         # left | right | "" (obstacle/fail)


@dataclass
class EnergyResult:
    """Structured result: the fail answer plus the derived telemetry.

    `events` is the re-playable core (every change in order); the summary fields
    are convenience aggregates over it, so future features (training, AI
    coaching, UI overlays) can consume either without recomputing anything.
    """
    start_energy: float = START_BAR
    end_energy: float = START_BAR
    min_energy: float = START_BAR
    max_energy: float = START_BAR
    did_reach_zero: bool = False
    fail_time: Optional[float] = None          # seconds, None when the run never failed
    time_in_danger: float = 0.0                # seconds spent below 30% energy
    final_charge: float = 0.0                  # Σ positive changes
    total_drain: float = 0.0                   # Σ |negative changes|
    drain_by_reason: dict = field(default_factory=dict)
    charge_by_hand: dict = field(default_factory=dict)
    drain_by_hand: dict = field(default_factory=dict)
    obstacle_hits: list = field(default_factory=list)   # recorded entry checkpoints
    events: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "start_energy": round(self.start_energy, 6),
            "end_energy": round(self.end_energy, 6),
            "min_energy": round(self.min_energy, 6),
            "max_energy": round(self.max_energy, 6),
            "did_reach_zero": self.did_reach_zero,
            "fail_time": (round(self.fail_time, 3) if self.fail_time is not None else None),
            "time_in_danger": round(self.time_in_danger, 3),
            "final_charge": round(self.final_charge, 6),
            "total_drain": round(self.total_drain, 6),
            "drain_by_reason": {k: round(v, 6) for k, v in sorted(self.drain_by_reason.items())},
            "charge_by_hand": {k: round(v, 6) for k, v in sorted(self.charge_by_hand.items())},
            "drain_by_hand": {k: round(v, 6) for k, v in sorted(self.drain_by_hand.items())},
            "obstacle_hits": self.obstacle_hits,
            "events": [{"t": round(e.t, 3), "d": round(e.delta, 6),
                        "e": round(e.energy, 6), "r": e.reason, "h": e.hand} for e in self.events],
        }


def _hand_of(note) -> str:
    """Which hand the change belongs to (informational only — the game's own
    counter is hand-agnostic). `NoteEvent.saber` already resolves this the
    official way (cut saber for good/bad, note colour otherwise)."""
    try:
        return getattr(note, "saber", "") or ""
    except Exception:                              # noqa: BLE001 — never break the run
        return ""


def _scoring_type(note) -> int:
    """NoteParams.scoring_type decoded from the noteID (official encoding):
    noteID = scoringType*10000 + lineIndex*1000 + noteLineLayer*100 + colorType*10 + cutDirection."""
    params = getattr(note, "params", None)
    return int(getattr(params, "scoring_type", SCORING_NORMAL) or SCORING_NORMAL)


def simulate_energy(notes: Iterable, walls: Iterable = (), cfg: Optional[EnergyConfig] = None,
                    scoring_types: Optional[dict] = None,
                    frame_times: Optional[Iterable] = None) -> EnergyResult:
    """Run the game's energy counter over one replay.

    notes:   parsed NoteEvent objects (need event_time + event_type).
    walls:   parsed WallEvent objects; `energy` non-zero marks a head-into-obstacle
             entry (the recorded remaining energy is a checkpoint, see the module doc).
    scoring_types: {note_id: gameplay_type} from the map, used to tell burst slider
             heads/elements from normal notes (no local map uses them today).
    frame_times: optional output of frame timestamps; unused for the drain itself
             (see the obstacle limitation) but reserved for full dwell integration.
    """
    cfg = cfg or EnergyConfig()
    scoring_types = scoring_types or {}
    res = EnergyResult(start_energy=cfg.start_energy)
    energy = cfg.start_energy
    zero_reached = False

    def apply(delta: float, t: float, reason: str, hand: str = "") -> None:
        nonlocal energy, zero_reached
        if zero_reached:
            return                                   # game: the fail is latched
        if delta < 0:
            if energy <= 0.0:
                return                               # game: already empty
            if cfg.insta_fail:
                new = 0.0                            # instaFail: any hit empties it
            elif cfg.energy_type == "battery":
                new = energy - 1.0 / float(cfg.battery_lives)   # battery: whole cell
            else:
                new = energy + delta
        elif delta > 0:
            if cfg.energy_type != "bar":
                return                               # only Bar mode charges back
            if energy >= ENERGY_CAP:
                return
            new = energy + delta
            if new >= ENERGY_CAP:
                new = ENERGY_CAP
        else:
            return
        if delta < 0:
            res.total_drain += abs(new - energy) if cfg.energy_type != "battery" else abs(delta)
            res.drain_by_reason[reason] = res.drain_by_reason.get(reason, 0.0) + abs(delta)
            if hand:
                res.drain_by_hand[hand] = res.drain_by_hand.get(hand, 0.0) + abs(delta)
        else:
            res.final_charge += (new - energy)
            if hand:
                res.charge_by_hand[hand] = res.charge_by_hand.get(hand, 0.0) + (new - energy)
        energy = new
        if energy <= FAIL_EPSILON:
            energy = 0.0
            zero_reached = True
            res.did_reach_zero = True
            res.fail_time = t
            res.events.append(EnergyEvent(t, -0.0, 0.0, "fail", ""))
        else:
            res.events.append(EnergyEvent(t, delta, energy, reason, hand))
        res.min_energy = min(res.min_energy, energy)
        res.max_energy = max(res.max_energy, energy)

    # ---- obstacle entries and note events merged in game time order ----
    # The game applies note-driven changes in LateUpdate and the obstacle drain
    # every frame the head is inside one; both are stamped here with their own
    # time, so the merged list keeps the same ordering the engine produced.
    changes: list[tuple[float, str, object]] = []
    for n in notes:
        if getattr(n, "event_time", None) is not None:
            changes.append((float(n.event_time), "note", n))
    for w in walls or ():
        recorded = float(getattr(w, "energy", 0.0) or 0.0)
        if recorded:
            changes.append((float(getattr(w, "time", 0.0) or 0.0), "obstacle", w))
    changes.sort(key=lambda c: c[0])

    previous_t: Optional[float] = None
    for t, kind, item in changes:
        if kind == "note":
            n = item
            etype = int(getattr(n, "event_type", GOOD) or 0)
            stype = _scoring_type(n)
            if stype == SCORING_BURST_ELEMENT:
                gt = GT_BURST_ELEMENT
            elif getattr(n, "is_bomb", False):
                gt = GT_BOMB
            else:
                gt = GT_NORMAL
            hand = _hand_of(n)
            if etype == BOMB or gt == GT_BOMB:
                apply(-DRAIN_BOMB, t, "bomb", hand)
            elif gt == GT_BURST_ELEMENT:
                if etype == GOOD:
                    apply(CHARGE_GOOD_BURST, t, "good_burst", hand)
                elif etype == BAD:
                    apply(-DRAIN_BAD_BURST, t, "bad_burst", hand)
                else:
                    apply(-DRAIN_MISS_BURST, t, "miss_burst", hand)
            else:
                if etype == GOOD:
                    apply(CHARGE_GOOD, t, "good", hand)
                elif etype == BAD:
                    apply(-DRAIN_BAD, t, "bad", hand)
                else:
                    apply(-DRAIN_MISS, t, "miss", hand)
            # danger time = time spent with a low bar (derived telemetry, cheap here)
            if previous_t is not None and res.min_energy < 0.3:
                res.time_in_danger += max(0.0, t - previous_t)
        else:
            w = item
            res.obstacle_hits.append({
                "t": round(t, 3),
                "recorded_energy": round(float(getattr(w, "energy", 0.0) or 0.0), 6),
                "wall_id": getattr(w, "wall_id", None),
            })
            apply(-OBSTACLE_DRAIN_PER_SECOND * cfg.obstacle_hit_seconds, t, "obstacle")
        previous_t = t

    res.end_energy = energy
    res.min_energy = min(res.min_energy, energy)
    return res
