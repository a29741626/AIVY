"""
fault_detection.py
==================

Fault detector for the Ivy Bridge sandbox stations (SBX-001, SBX-002, SBX-003).

One call does everything:

    from data_ingestion import build_dataset
    from fault_detection import analyse_station

    df = build_dataset(station_id="SBX-002", days=30)
    result = analyse_station(df, "SBX-002")
    print(result["daily"])

For every day it returns:
    status       - healthy / sub_healthy / broken
    fault_types  - which pattern(s) were seen (a description, NOT a diagnosis)
    explanation  - one plain-English sentence a homeowner could act on

It also returns station-level checks that a single day cannot show:
    data_quality   - energy-balance check + battery/load sign test
    trends         - gradual PV decline and battery capacity shrink over time

Every rule below comes from what was learned investigating the three stations:

  1. Baseline = typical PV for each (time-of-day period, 75 W/m^2 GHI bucket).
     GHI-only buckets were too loose; exact 5-minute slots left too little
     data per bucket. Broad periods + 75 W/m^2 bins was the balance that worked.
  2. A reading is "low" when PV < 70% of its bucket's median. (The p25 was
     too lenient: a fault that repeats across days drags p25 down and hides
     itself.)
  3. Curtailment is NOT a fault. When the battery is full, the system
     deliberately turns PV down. The "full" level is LEARNED per station
     (99th percentile of its own SOC) - SBX-003 is full at 80%, not ~100%.
     Using a fixed 98% overstated SBX-003's fault rate about 7x.
     PV is also capped (normal) when the system cannot export and the
     battery is already charging at its maximum rate.
     "Expected" PV is learned ONLY from moments the system was free to
     produce - otherwise curtailed moments drag "normal" down and hide faults.
  4. Judge whole days, not single readings. One low 5-minute reading is
     ordinary noise (a passing cloud); a day with many is a real signal.
  5. Label days RELATIVE to the same station's other days. A fixed
     percentage cut-off called healthy SBX-001 "broken". A day is only
     "broken" if it is clearly separated from the rest by a large gap.
  6. Describe patterns, never claim a cause - per the client's instruction.
"""

import numpy as np
import pandas as pd

# =============================================================================
# Settings (all validated on SBX-001 / 002 / 003)
# =============================================================================

REQUIRED_COLUMNS = ["timestamp", "presentation_date", "pv", "load", "battery",
                    "grid", "soc", "ghi_w_m2"]

SLOT_MINUTES = 5
SLOTS_PER_DAY = 288

# --- Baseline ---------------------------------------------------------------
GHI_BIN_WIDTH = 75
TIME_PERIOD_BINS = [-1, 4, 8, 12, 16, 20, 24]
TIME_PERIOD_LABELS = ["night", "early_morning", "midday", "afternoon",
                      "evening", "late_evening"]
DEVIATION_THRESHOLD = 0.7          # low = PV below 70% of bucket median
MIN_BUCKET_SAMPLES = 12            # a group needs >= 12 free-production readings
                                   # before its "normal" is trusted, so one bad
                                   # day can never define normal on its own
MIN_EXPECTED_W = 100               # ignore moments when expected PV is tiny
                                   # (sunrise/sunset): 70% of almost nothing is
                                   # just noise - seen clearly on SBX-003

# --- Curtailment ------------------------------------------------------------
CURTAILMENT_PERCENTILE = 99        # station's own "full" SOC level
CURTAILMENT_BUFFER = 1.0           # within 1 point of full counts as full
CHARGE_LIMIT_PERCENTILE = 99       # station's own maximum charging rate
CHARGE_LIMIT_SHARE = 0.95          # charging at >=95% of it counts as "at max"
ZERO_EXPORT_W = 50                 # grid within +/-50 W counts as "not exporting"

# --- Day classification (relative to the station's own days) ----------------
MIN_DAYS_FOR_CLASSIFICATION = 5
SUB_HEALTHY_Z = 1.5                # robust z-score for "worth a check"
MIN_SUB_HEALTHY_PCT = 2.0          # a day needs at least 2% low readings
                                   # (~30 min of daylight) to be worth a check
MIN_SPREAD_PCT = 1.0               # never treat spread as less than 1 point,
                                   # so near-identical ~0% days can't look unusual
BROKEN_GAP_RATIO = 10.0            # gap needed to call a top group "broken"
                                   # (SBX-001 natural noise: ~4-6x; real
                                   #  injected faults: 80x+)

# --- Data quality -----------------------------------------------------------
ENERGY_BALANCE_TOLERANCE_W = 50
SIGN_ISSUE_IMPROVEMENT_PTS = 20    # flipping a sign must cut violations by
                                   # at least 20 percentage points to count
BATTERY_DISAGREE_PCT = 30          # battery says "charging" while SOC falls in
                                   # >=30% of those moments -> battery sign wrong

# --- Pattern (fault type) detection -----------------------------------------
STRONG_SUN_GHI = 200               # W/m^2 - clearly daytime, clearly sunny
ZERO_PV_W = 10                     # PV at or below this counts as "zero"
SUDDEN_ZERO_MIN_SLOTS = 6          # 30 minutes of zero output in strong sun
DROPOUT_MIN_SLOTS = 6              # 30 minutes of all-channels-exactly-zero
FLAT_WINDOW_SLOTS = 6              # 30-minute window for flattening check
FLAT_PV_TOLERANCE = 0.02           # PV varies < 2% across the window...
FLAT_GHI_MIN_CHANGE = 0.10         # ...while sunlight varies >= 10%
FLAT_MIDDAY_HOURS = (10, 14)
SPIKE_FACTOR = 1.25                # PV above 1.25x the station's normal peak
SPIKE_PERCENTILE = 99.9            # normal peak = 99.9th percentile of DAYTIME PV
                                   # (night zeros must not pull it down - that
                                   #  wrongly flagged SBX-001's normal 10.9 kW peaks)

# --- Multi-day trends -------------------------------------------------------
TREND_MIN_DAYS = 10
TREND_DECLINE_PCT = -10.0          # >=10% drop over the period...
TREND_MIN_CORRELATION = -0.5       # ...and a consistent downward trend

# Patterns that are always worth a look, whatever the day's percentage says.
ESCALATING_PATTERNS = {"sudden_drop_to_zero", "midday_flattening",
                       "abnormal_spike", "missing_data",
                       "communication_dropout"}


# =============================================================================
# 0. Input checks
# =============================================================================

def prepare_input(df):
    """
    Checks required columns, fixes types, removes fully blank rows, and
    sorts by time. Returns (clean_df, number_of_rows_dropped).
    Never modifies the caller's DataFrame.
    """
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Input is missing required columns: {missing}")

    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["presentation_date"] = df["presentation_date"].astype(str)

    before = len(df)
    df = df.dropna(subset=["pv", "load", "battery", "grid", "soc", "ghi_w_m2"])
    dropped = before - len(df)

    df = df.sort_values("timestamp").reset_index(drop=True)
    if len(df) == 0:
        raise ValueError("No usable rows after removing blank readings.")
    return df, dropped


# =============================================================================
# 1. Data quality (station level)
# =============================================================================

def check_data_quality(df):
    """
    Energy balance: pv = load + battery + grid must hold every slot.

    Also tests whether flipping the sign of battery or load fixes it. When
    load and battery are about the same size (e.g. at night), flipping EITHER
    one balances the equation, so the maths alone can't say which is wrong.
    The battery's own charge level settles it: if SOC is FALLING while the
    battery reports CHARGING (positive), the battery's sign is wrong.
    This is how SBX-002's battery sign bug is identified. On SBX-003,
    flipping load made things worse, so its negative load is genuine.
    """
    def violation_pct(pv, load, battery, grid):
        imbalance = (pv - (load + battery + grid)).abs()
        return float((imbalance > ENERGY_BALANCE_TOLERANCE_W).mean() * 100)

    pv, load, battery, grid = df["pv"], df["load"], df["battery"], df["grid"]
    original = violation_pct(pv, load, battery, grid)
    battery_flipped = violation_pct(pv, load, -battery, grid)
    load_flipped = violation_pct(pv, -load, battery, grid)

    # Battery direction vs SOC direction
    d = df.sort_values("timestamp")
    soc_next = d["soc"].shift(-1)
    same_day = d["presentation_date"].shift(-1) == d["presentation_date"]
    falling = same_day & (soc_next < d["soc"]) & (d["battery"].abs() > ENERGY_BALANCE_TOLERANCE_W)
    battery_disagrees_pct = (float((d.loc[falling, "battery"] > 0).mean() * 100)
                             if falling.any() else 0.0)

    battery_helps = original - battery_flipped >= SIGN_ISSUE_IMPROVEMENT_PTS
    load_helps = original - load_flipped >= SIGN_ISSUE_IMPROVEMENT_PTS
    battery_wrong = battery_disagrees_pct >= BATTERY_DISAGREE_PCT

    suspected = []
    if battery_wrong:
        # Direct physical evidence: the battery reports charging while its own
        # charge level is falling. This is enough on its own - if the sign is
        # wrong only part of the time (e.g. only at night, as on SBX-002),
        # flipping the whole column can fix one part and break another, so
        # the overall flip test alone could miss it.
        suspected.append("battery")
    else:
        if battery_helps:
            suspected.append("battery")
        if load_helps:
            suspected.append("load")

    return {
        "energy_balance_violation_pct": round(original, 1),
        "violation_pct_if_battery_sign_flipped": round(battery_flipped, 1),
        "violation_pct_if_load_sign_flipped": round(load_flipped, 1),
        "battery_says_charging_while_soc_falls_pct": round(battery_disagrees_pct, 1),
        "suspected_sign_issue": suspected,
    }


# =============================================================================
# 2. Baseline and low-output flags
# =============================================================================

def build_baseline(df, ghi_bin_width=GHI_BIN_WIDTH):
    """
    Adds time_period and ghi_bin columns, and returns the typical PV
    (median, p25, p75, count) for each (time_period, ghi_bin) group.

    The typical PV is learned ONLY from readings where the system was free
    to produce (is_curtailed == False). Learning from curtailed moments
    would drag "normal" down and let real faults hide inside it.
    Groups with no free-production readings get no expectation (no flags).
    """
    df = df.copy()
    df["time_period"] = pd.cut(df["timestamp"].dt.hour,
                               bins=TIME_PERIOD_BINS,
                               labels=TIME_PERIOD_LABELS)
    df["ghi_bin"] = (df["ghi_w_m2"] // ghi_bin_width) * ghi_bin_width

    training = df[~df["is_curtailed"]] if "is_curtailed" in df.columns else df
    baseline = training.groupby(["time_period", "ghi_bin"], observed=True)["pv"].agg(
        expected_median="median",
        expected_p25=lambda x: x.quantile(0.25),
        expected_p75=lambda x: x.quantile(0.75),
        sample_count="count",
    ).reset_index()
    baseline = baseline[baseline["sample_count"] >= MIN_BUCKET_SAMPLES]
    return df, baseline


def learn_pv_per_ghi(df):
    """
    Station-wide solar-to-sunlight ratio (W of PV per W/m^2 of GHI), learned
    from sunny moments when the system was free to produce. Used as a second,
    independent expectation that one faulty day cannot distort.
    """
    free = df[(~df["is_curtailed"]) & (df["ghi_w_m2"] >= STRONG_SUN_GHI)]
    if free.empty:
        return None
    ratio = float((free["pv"] / free["ghi_w_m2"]).median())
    return ratio if ratio > 0 else None


def apply_baseline(df, baseline, threshold=DEVIATION_THRESHOLD):
    """Attaches each reading's expected PV and flags readings below 70% of it."""
    df = df.merge(baseline, on=["time_period", "ghi_bin"], how="left",
                  validate="many_to_one")
    df["deviation"] = df["pv"] - df["expected_median"]
    df["pv_below_threshold"] = ((df["expected_median"] >= MIN_EXPECTED_W) &
                                (df["pv"] < df["expected_median"] * threshold))
    return df


# =============================================================================
# 3. Curtailment (learned per station)
# =============================================================================

def learn_curtailment_ceiling(df, percentile=CURTAILMENT_PERCENTILE):
    """The SOC level this station actually treats as full (e.g. 80% on SBX-003)."""
    return float(df["soc"].quantile(percentile / 100))


def learn_charge_limit(df, percentile=CHARGE_LIMIT_PERCENTILE):
    """This station's own maximum charging rate (W), learned from its data."""
    charging = df.loc[df["battery"] > 0, "battery"]
    return float(charging.quantile(percentile / 100)) if not charging.empty else 0.0


def flag_curtailment(df, ceiling, charge_limit, buffer=CURTAILMENT_BUFFER):
    """
    is_curtailed = PV is deliberately limited, which is normal behaviour:
      - battery_full:   SOC at the station's own learned "full" level, or
      - charge_limited: not exporting AND the battery is already charging at
                        its maximum rate, so extra PV has nowhere to go.
    """
    df = df.copy()
    df["battery_full"] = df["soc"] >= (ceiling - buffer)
    if charge_limit > 0:
        df["charge_limited"] = ((df["grid"].abs() <= ZERO_EXPORT_W) &
                                (df["battery"] >= CHARGE_LIMIT_SHARE * charge_limit))
    else:
        df["charge_limited"] = False
    df["is_curtailed"] = df["battery_full"] | df["charge_limited"]
    return df


def flag_genuine_candidates(df):
    """Low PV that is NOT explained by a full battery."""
    df = df.copy()
    df["genuine_fault_candidate"] = df["pv_below_threshold"] & ~df["is_curtailed"]
    return df


# =============================================================================
# 4. Daily aggregation
# =============================================================================

def aggregate_daily(df):
    daily = df.groupby("presentation_date").agg(
        pct_flagged=("genuine_fault_candidate", "mean"),
        readings_flagged=("genuine_fault_candidate", "sum"),
        readings=("genuine_fault_candidate", "count"),
    ).reset_index()
    daily["pct_flagged"] = daily["pct_flagged"] * 100
    daily["readings_flagged"] = daily["readings_flagged"].astype(int)
    return daily


# =============================================================================
# 5. Relative day classification
# =============================================================================

def classify_days_relative(daily):
    """
    Labels each day against the SAME station's other days.

    - z_score: how far the day's flagged % is from the station's median,
      using median + MAD (not mean + std) so bad days don't hide themselves.
    - A "broken" group needs a real break: the biggest drop among the top
      half of days must be >= BROKEN_GAP_RATIO times the typical drop.
      Typical drop is measured only among the top half, so a cluster of
      near-zero days at the bottom cannot make normal gaps look huge.
    - Every day above that break gets the same label, so a higher day can
      never be rated less severe than a lower one.
    """
    daily = daily.sort_values("pct_flagged", ascending=False).reset_index(drop=True)
    n = len(daily)

    if n < MIN_DAYS_FOR_CLASSIFICATION:
        daily["z_score"] = 0.0
        daily["gap_ratio"] = 0.0
        daily["status"] = "healthy"
        return daily

    values = daily["pct_flagged"].to_numpy(dtype=float)

    median = np.median(values)
    mad = np.median(np.abs(values - median))
    spread = max(1.4826 * mad, MIN_SPREAD_PCT)
    daily["z_score"] = (values - median) / spread

    gaps = values[:-1] - values[1:]            # gap from rank i down to rank i+1
    top_n = min(max(1, n // 2), n - 1)
    top_gaps = gaps[:top_n]
    positive = top_gaps[top_gaps > 0]
    typical_gap = float(np.median(positive)) if positive.size else 1.0
    if typical_gap <= 0:
        typical_gap = 1.0
    gap_ratios = gaps / typical_gap
    daily["gap_ratio"] = np.append(gap_ratios, 0.0)

    break_rank = None
    best = int(np.argmax(gap_ratios[:top_n]))
    if gap_ratios[best] >= BROKEN_GAP_RATIO:
        break_rank = best

    statuses = []
    for rank, (z, pct) in enumerate(zip(daily["z_score"], values)):
        elevated = z >= SUB_HEALTHY_Z and pct >= MIN_SUB_HEALTHY_PCT
        if break_rank is not None and rank <= break_rank and elevated:
            statuses.append("broken")
        elif elevated:
            statuses.append("sub_healthy")
        else:
            statuses.append("healthy")
    daily["status"] = statuses
    return daily


# =============================================================================
# 6. Fault-type (pattern) detection for each day
# =============================================================================

def _runs(mask):
    """Returns (start_index, length) for each run of True values."""
    mask = np.asarray(mask, dtype=bool)
    runs, start = [], None
    for i, v in enumerate(mask):
        if v and start is None:
            start = i
        elif not v and start is not None:
            runs.append((start, i - start))
            start = None
    if start is not None:
        runs.append((start, len(mask) - start))
    return runs


def _time_range(day, start, length):
    t0 = day["timestamp"].iloc[start]
    t1 = day["timestamp"].iloc[start + length - 1] + pd.Timedelta(minutes=SLOT_MINUTES)
    return f"{t0:%H:%M}-{t1:%H:%M}"


def detect_day_patterns(day, spike_limit, is_edge_day, pv_per_ghi=None):
    """
    Returns a list of (pattern_name, evidence_text) for one day.
    Patterns describe WHAT was seen, never WHY.
    """
    day = day.sort_values("timestamp").reset_index(drop=True)
    found = []

    # --- missing data / incomplete day --------------------------------------
    date = pd.Timestamp(day["presentation_date"].iloc[0])
    expected = pd.date_range(date, periods=SLOTS_PER_DAY, freq=f"{SLOT_MINUTES}min")
    present = set(day["timestamp"])
    missing_mask = np.array([t not in present for t in expected])
    n_missing = int(missing_mask.sum())
    if n_missing > 0:
        missing_runs = _runs(missing_mask)
        touches_edge = all(s == 0 or s + l == SLOTS_PER_DAY for s, l in missing_runs)

    sunny = day["ghi_w_m2"] >= STRONG_SUN_GHI
    not_curtailed = ~day["is_curtailed"]

    # --- communication dropout: every channel exactly zero in daylight -------
    all_zero = ((day["pv"] == 0) & (day["load"] == 0) &
                (day["battery"] == 0) & (day["grid"] == 0) & sunny)
    dropout = [r for r in _runs(all_zero) if r[1] >= DROPOUT_MIN_SLOTS]
    if dropout:
        s, l = max(dropout, key=lambda r: r[1])
        found.append(("communication_dropout",
                      f"all readings were exactly zero {_time_range(day, s, l)}"))

    # --- sudden drop to zero in strong sun (not a dropout, not curtailment) --
    zero_pv = (day["pv"] <= ZERO_PV_W) & sunny & not_curtailed & ~all_zero
    zero_runs = [r for r in _runs(zero_pv) if r[1] >= SUDDEN_ZERO_MIN_SLOTS]
    if zero_runs:
        s, l = max(zero_runs, key=lambda r: r[1])
        found.append(("sudden_drop_to_zero",
                      f"no solar output {_time_range(day, s, l)} despite strong sunlight"))

    # --- midday flattening: PV flat and low while sunlight keeps changing ----
    h0, h1 = FLAT_MIDDAY_HOURS
    midday = day["timestamp"].dt.hour.between(h0, h1 - 1)
    pv_roll = day["pv"].rolling(FLAT_WINDOW_SLOTS)
    ghi_roll = day["ghi_w_m2"].rolling(FLAT_WINDOW_SLOTS)
    pv_range = pv_roll.max() - pv_roll.min()
    ghi_range = ghi_roll.max() - ghi_roll.min()
    pv_mean = pv_roll.mean()
    ghi_mean = ghi_roll.mean()
    window_curtailed = day["is_curtailed"].astype(float).rolling(FLAT_WINDOW_SLOTS).max()

    flat = (midday & (pv_mean > ZERO_PV_W) & (ghi_mean >= STRONG_SUN_GHI)
            & (pv_range <= FLAT_PV_TOLERANCE * pv_mean)
            & (ghi_range >= FLAT_GHI_MIN_CHANGE * ghi_mean)
            & (window_curtailed == 0))
    if pv_per_ghi is not None:
        # flat AND well below what this much sunlight normally gives
        flat = flat & (pv_mean < DEVIATION_THRESHOLD * pv_per_ghi * ghi_mean)
    else:
        flat = flat & False
    if flat.fillna(False).any():
        idx = int(np.flatnonzero(flat.fillna(False).to_numpy())[0])
        start = idx - FLAT_WINDOW_SLOTS + 1
        found.append(("midday_flattening",
                      f"output stayed flat at ~{day['pv'].iloc[idx]:.0f} W around "
                      f"{_time_range(day, start, FLAT_WINDOW_SLOTS)} while sunlight changed"))

    # --- abnormal spike: far above anything normal for this station ---------
    if spike_limit is not None:
        spikes = day["pv"] > spike_limit
        if spikes.any():
            peak = day.loc[spikes, "pv"].max()
            t = day.loc[day["pv"].idxmax(), "timestamp"]
            found.append(("abnormal_spike",
                          f"output jumped to {peak:.0f} W at {t:%H:%M}, well above "
                          f"this system's normal maximum"))

    return found


# =============================================================================
# 7. Multi-day trends (station level)
# =============================================================================

def _trend(series):
    """Percentage change over the period from a straight-line fit, plus correlation."""
    s = series.dropna()
    if len(s) < TREND_MIN_DAYS or s.mean() == 0 or s.std() == 0:
        return None
    x = np.arange(len(s), dtype=float)
    y = s.to_numpy(dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    start = intercept
    end = intercept + slope * (len(s) - 1)
    if start <= 0:
        return None
    change_pct = (end - start) / start * 100
    corr = float(np.corrcoef(x, y)[0, 1])
    return {"change_pct": round(float(change_pct), 1),
            "correlation": round(corr, 2), "days_used": int(len(s))}


def detect_gradual_pv_decline(df):
    """
    Daily performance ratio = actual PV / expected PV, using only sunny,
    non-curtailed daytime readings. A steady fall over the period suggests
    gradual degradation (e.g. dirt, ageing) - something no single day shows.
    """
    mask = (df["ghi_w_m2"] >= STRONG_SUN_GHI) & ~df["is_curtailed"] & \
           (df["expected_median"] > 0)
    sub = df[mask]
    if sub.empty:
        return {"detected": False, "reason": "not enough sunny, non-curtailed readings"}
    ratio = sub.groupby("presentation_date").apply(
        lambda d: d["pv"].sum() / d["expected_median"].sum(), include_groups=False
    ).sort_index()
    result = _trend(ratio)
    if result is None:
        return {"detected": False, "reason": f"need at least {TREND_MIN_DAYS} usable days"}
    result["detected"] = (result["change_pct"] <= TREND_DECLINE_PCT and
                          result["correlation"] <= TREND_MIN_CORRELATION)
    return result


def detect_battery_capacity_shrink(df):
    """
    Estimates usable capacity each day as Wh needed per 1% of SOC while charging
    (only slots where the battery is charging AND SOC actually rose, which
    also excludes SBX-002's wrongly-signed night readings). A steady fall
    means the battery holds less energy than before.
    """
    d = df.sort_values("timestamp").copy()
    d["soc_change"] = d["soc"].shift(-1) - d["soc"]
    same_day = d["presentation_date"].shift(-1) == d["presentation_date"]
    charging = (d["battery"] > 100) & (d["soc_change"] > 0) & same_day
    c = d[charging].copy()
    if c.empty:
        return {"detected": False, "reason": "no clean charging periods found"}
    c["energy_wh"] = c["battery"] * SLOT_MINUTES / 60
    per_day = c.groupby("presentation_date").agg(
        energy=("energy_wh", "sum"), soc_gain=("soc_change", "sum"))
    per_day = per_day[per_day["soc_gain"] >= 10]      # need a meaningful charge
    wh_per_pct = (per_day["energy"] / per_day["soc_gain"]).sort_index()
    result = _trend(wh_per_pct)
    if result is None:
        return {"detected": False, "reason": f"need at least {TREND_MIN_DAYS} days with a full charge"}
    result["detected"] = (result["change_pct"] <= TREND_DECLINE_PCT and
                          result["correlation"] <= TREND_MIN_CORRELATION)
    return result


# =============================================================================
# 8. Plain-English explanation
# =============================================================================

PATTERN_WORDS = {
    "sudden_drop_to_zero": "solar output dropped to zero",
    "midday_flattening": "solar output went flat at midday",
    "abnormal_spike": "an abnormal spike in solar output",
    "missing_data": "missing readings",
    "communication_dropout": "the system stopped reporting",
    "incomplete_day": "only part of the day was recorded",
}


def explain_day(status, pct, patterns, worst_time):
    if status == "healthy" and not patterns:
        return (f"Solar output matched what the weather allowed "
                f"({pct:.1f}% of readings were low - normal).")

    if status == "healthy" and patterns:
        # only informational patterns (incomplete_day) reach here
        return "Healthy. " + "; ".join(e for _, e in patterns) + "."

    parts = []
    if patterns:
        parts.append("; ".join(e for _, e in patterns))
    else:
        when = f", mostly around {worst_time}" if worst_time else ""
        parts.append(f"solar output was lower than the weather allowed for "
                     f"{pct:.1f}% of the day{when}")

    if status == "broken":
        advice = "Recommend an inspection."
    else:
        advice = "Worth a check - not yet a confirmed fault."
    text = parts[0]
    return text[0].upper() + text[1:] + ". " + advice


# =============================================================================
# 9. Cross-check against a healthy reference station
# =============================================================================

def cross_validate_against_reference(df_suspect, df_reference):
    """
    At the exact same timestamps (same Adelaide weather), does the suspect
    station produce LESS than a healthy one? If not, its flags are most
    likely a baseline artefact. Returns None when the date ranges don't overlap.
    """
    flagged = df_suspect[df_suspect["genuine_fault_candidate"]]
    ref = df_reference[["timestamp", "pv"]].copy()
    ref["timestamp"] = pd.to_datetime(ref["timestamp"])
    merged = flagged[["timestamp", "pv"]].merge(ref, on="timestamp", how="inner",
                                                suffixes=("_suspect", "_reference"))
    if merged.empty:
        return None
    s, r = merged["pv_suspect"].median(), merged["pv_reference"].median()
    return {"matched_readings": int(len(merged)),
            "suspect_median_pv": round(float(s), 1),
            "reference_median_pv": round(float(r), 1),
            "suspect_underperforms": bool(s < r)}


# =============================================================================
# 10. Main entry point
# =============================================================================

def analyse_station(df, station_id, reference_df=None):
    """
    Runs the full pipeline on the output of data_ingestion.build_dataset().

    Returns a dict:
        daily             - one row per day: status, fault_types, explanation
        df                - every reading with all flags attached
        soc_ceiling       - the learned "battery full" level
        charge_limit_w    - the learned maximum charging rate
        data_quality      - energy balance + sign test
        trends            - gradual PV decline, battery capacity shrink
        cross_validation  - vs reference_df (or None)
        rows_dropped      - fully blank rows removed
    """
    df, rows_dropped = prepare_input(df)
    data_quality = check_data_quality(df)

    ceiling = learn_curtailment_ceiling(df)
    charge_limit = learn_charge_limit(df)
    df = flag_curtailment(df, ceiling, charge_limit)

    df, baseline = build_baseline(df)
    df = apply_baseline(df, baseline)
    df = flag_genuine_candidates(df)

    daily = classify_days_relative(aggregate_daily(df))

    pv_per_ghi = learn_pv_per_ghi(df)
    daytime_pv = df.loc[df["pv"] > ZERO_PV_W, "pv"]
    spike_limit = (float(daytime_pv.quantile(SPIKE_PERCENTILE / 100)) * SPIKE_FACTOR
                   if not daytime_pv.empty else None)
    first_day = df["presentation_date"].min()
    last_day = df["presentation_date"].max()

    fault_types, explanations = [], []
    for i, row in daily.iterrows():
        day = df[df["presentation_date"] == row["presentation_date"]]
        is_edge = row["presentation_date"] in (first_day, last_day)
        patterns = detect_day_patterns(day, spike_limit, is_edge, pv_per_ghi)

        status = row["status"]
        if status == "healthy" and any(p in ESCALATING_PATTERNS for p, _ in patterns):
            status = "sub_healthy"
            daily.at[i, "status"] = status

        worst_time = None
        candidates = day[day["genuine_fault_candidate"]]
        if not candidates.empty:
            worst = candidates.loc[candidates["deviation"].idxmin()]
            worst_time = f"{worst['timestamp']:%H:%M}"

        fault_types.append(", ".join(p for p, _ in patterns) if patterns else "none")
        explanations.append(explain_day(status, row["pct_flagged"], patterns, worst_time))

    daily["fault_types"] = fault_types
    daily["explanation"] = explanations
    daily.insert(0, "station_id", station_id)
    daily = daily.sort_values("presentation_date").reset_index(drop=True)
    daily["pct_flagged"] = daily["pct_flagged"].round(2)
    daily["z_score"] = daily["z_score"].round(2)
    daily["gap_ratio"] = daily["gap_ratio"].round(2)

    trends = {"gradual_pv_decline": detect_gradual_pv_decline(df),
              "battery_capacity_shrink": detect_battery_capacity_shrink(df)}

    cross = cross_validate_against_reference(df, reference_df) if reference_df is not None else None

    return {"daily": daily, "df": df, "soc_ceiling": round(ceiling, 1),
            "charge_limit_w": round(charge_limit, 0),
            "data_quality": data_quality, "trends": trends,
            "cross_validation": cross, "rows_dropped": rows_dropped}


def summarise(result):
    """Prints a short, readable report for one station."""
    d = result["daily"]
    sid = d["station_id"].iloc[0]
    print(f"===== {sid} =====")
    print(f"Battery 'full' level learned: {result['soc_ceiling']}%   "
          f"max charge rate learned: {result['charge_limit_w']:.0f} W")
    dq = result["data_quality"]
    print(f"Energy balance violations: {dq['energy_balance_violation_pct']}%"
          + (f"  (suspected sign issue: {', '.join(dq['suspected_sign_issue'])})"
             if dq["suspected_sign_issue"] else ""))
    counts = d["status"].value_counts()
    print("Days: " + ", ".join(f"{k}={counts.get(k, 0)}"
                               for k in ["healthy", "sub_healthy", "broken"]))
    for name, t in result["trends"].items():
        if t.get("detected"):
            print(f"TREND: {name} detected ({t['change_pct']}% over {t['days_used']} days)")
    flagged = d[d["status"] != "healthy"]
    if not flagged.empty:
        print("\nDays worth attention:")
        print(flagged[["presentation_date", "pct_flagged", "status",
                       "fault_types", "explanation"]].to_string(index=False))
    if result["cross_validation"]:
        print(f"\nCross-check vs reference: {result['cross_validation']}")
    print()


if __name__ == "__main__":
    from data_ingestion import build_dataset

    reference = build_dataset(station_id="SBX-001", days=30)
    for sid in ["SBX-001", "SBX-002", "SBX-003"]:
        data = reference if sid == "SBX-001" else build_dataset(station_id=sid, days=30)
        res = analyse_station(data, sid,
                              reference_df=None if sid == "SBX-001" else reference)
        summarise(res)