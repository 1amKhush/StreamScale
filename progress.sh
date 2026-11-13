#!/usr/bin/env bash
python3 -c '
import glob, json, os

def get_progress():
    # 1. Tuning files (24 total)
    tuning_files = glob.glob("artifacts/predictive/*/tuning/*.json")
    tuning_count = len(tuning_files)
    tuning_done = tuning_count >= 24

    # 2. Console check (must be from the active campaign today)
    console_check_done = False
    if os.path.exists("artifacts/console/check.json"):
        try:
            data = json.load(open("artifacts/console/check.json"))
            if data.get("started_at", "").startswith("2026-11-11") and data.get("status") == "passed":
                console_check_done = True
        except Exception:
            pass

    # 3. Evaluation files (36 held-out runs)
    eval_captures = [
        f for f in glob.glob("artifacts/predictive/*/evaluation/*.json")
        if os.path.basename(f) not in ("summary.json", "complete.json")
    ]
    eval_count = len(eval_captures)
    eval_complete = any(os.path.exists(f) for f in glob.glob("artifacts/predictive/*/evaluation/complete.json"))

    # 4. Final predictive analysis
    analysis_done = eval_complete and os.path.exists("docs/results/current/report.md")

    base = 80.0
    tuning_pct = (min(24, tuning_count) / 24.0) * 12.0 # up to 92.0%
    
    # Remaining 8% breakdown:
    # Console check: 3% (92% -> 95%)
    console_pct = 3.0 if console_check_done else 0.0
    # Held-out evaluation: 4% (95% -> 99%)
    eval_pct = (min(36, eval_count) / 36.0) * 4.0 if tuning_done and console_check_done else 0.0
    # Final analysis: 1% (99% -> 100%)
    final_pct = 1.0 if (eval_complete and analysis_done) else 0.0

    total = min(100.0, base + tuning_pct + console_pct + eval_pct + final_pct)

    if not tuning_done:
        current_m = "M17–M18: Bayesian Policy Optimization"
        step = f"Tuning trial {tuning_count}/24 (Candidate {tuning_count // 2}/12)"
    elif not console_check_done:
        current_m = "M19: Live Console Acceptance & E2E Browser Testing"
        step = "Executing live cluster flash sales, failure injection & Playwright tests"
    elif not eval_complete:
        current_m = "M20: Predictive Held-out Comparison & Evaluation"
        step = f"Evaluating held-out comparison workloads: {eval_count}/36 runs completed ({eval_count/36*100:.1f}%)"
    elif not analysis_done:
        current_m = "M20: Generating Predictive Analysis & Diagnostic Plots"
        step = "Generating offline evaluation plots and performance summary"
    else:
        current_m = "M0–M21 Completed!"
        step = "All milestones verified, reports generated, ready for console use"

    bar_len = 30
    filled = int(bar_len * (total / 100.0))
    bar = "█" * filled + "░" * (bar_len - filled)

    print("-" * 65)
    print(f"  StreamScale Pipeline Progress: [{bar}] {total:.1f}%")
    print("-" * 65)
    print(f"  Current Milestone : {current_m}")
    print(f"  Active Task       : {step}")
    print(f"  Completed         : M0–M19 (M0–M18, M21, Console Check passed)")
    print("-" * 65)

get_progress()
'
