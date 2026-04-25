"""MCPDischarge evaluation dashboard.

Generates a small set of charts by running all 6 patient scenarios through the
async MCP-based DischargeCoordinationAgent.

Prereq: start MCP servers (FastMCP SSE):
  python src/servers/mcp_servers.py --all
"""

from __future__ import annotations

import sys
import asyncio
import json
import random
from pathlib import Path

# Ensure project root is on sys.path when running as a script:
#   python evaluation/eval_dashboard.py
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from src.agents.discharge_agent import DischargeCoordinationAgent, WorkflowMetrics, test_scope_violation

random.seed(42)
np.random.seed(42)

EVAL_DIR = Path(__file__).parent

PATIENTS = ["PAT-001", "PAT-002", "PAT-003", "PAT-004", "PAT-005", "PAT-006"]

BG = "#0d1117"
PANEL = "#161b22"
GRID = "#21262d"
BORDER = "#30363d"
TEXT = "#e6edf3"
DIM = "#8b949e"


async def run_all() -> list[dict]:
    agent = DischargeCoordinationAgent()
    results = []
    for pid in PATIENTS:
        r = await agent.orchestrate_discharge(pid)
        m = WorkflowMetrics.compute(r)
        results.append({"patient_id": pid, "result": r, "metrics": m})
    return results


def plot_manual_vs_mcp(results: list[dict], out_dir: Path):
    fig, axes = plt.subplots(1, 3, figsize=(16, 5))
    fig.patch.set_facecolor(BG)

    pats = [r["patient_id"] for r in results]
    mcp_calls = [r["result"]["mcp_tool_calls_total"] for r in results]
    manual_steps = [WorkflowMetrics.TRADITIONAL_MANUAL_STEPS["total_manual_handoffs"]] * len(pats)
    time_saved = [r["metrics"]["time_saved_minutes"] for r in results]
    success_rates = [r["result"]["tool_call_success_rate"] * 100 for r in results]

    x = np.arange(len(pats))
    w = 0.35

    ax = axes[0]
    ax.set_facecolor(PANEL)
    ax.bar(x - w / 2, manual_steps, w, label="Manual handoffs", color="#ff6b6b", alpha=0.85, edgecolor=BORDER)
    ax.bar(x + w / 2, mcp_calls, w, label="MCP tool calls", color="#1dd1a1", alpha=0.85, edgecolor=BORDER)
    ax.set_xticks(x)
    ax.set_xticklabels(pats, fontsize=8, color=DIM)
    ax.set_title("Manual Handoffs vs MCP Tool Calls", color=TEXT, fontsize=10, pad=8)
    ax.legend(facecolor=PANEL, labelcolor=DIM, fontsize=9)
    ax.grid(axis="y", color=GRID, lw=0.7)
    ax.spines[:].set_color(BORDER)
    ax.tick_params(colors=DIM)

    ax2 = axes[1]
    ax2.set_facecolor(PANEL)
    ax2.bar(x, time_saved, color="#48dbfb", alpha=0.85, edgecolor=BORDER, width=0.6)
    ax2.set_xticks(x)
    ax2.set_xticklabels(pats, fontsize=8, color=DIM)
    ax2.set_ylabel("Minutes saved vs manual", color=DIM)
    ax2.set_title("Time Saved per Discharge", color=TEXT, fontsize=10, pad=8)
    ax2.grid(axis="y", color=GRID, lw=0.7)
    ax2.spines[:].set_color(BORDER)
    ax2.tick_params(colors=DIM)

    ax3 = axes[2]
    ax3.set_facecolor(PANEL)
    alert_counts = [len(r["result"]["alerts"]) for r in results]
    ax3.bar(x, success_rates, color="#1dd1a1", alpha=0.75, edgecolor=BORDER, width=0.6, label="Success %")
    ax3b = ax3.twinx()
    ax3b.plot(x, alert_counts, "o-", color="#feca57", lw=2.5, ms=8, label="Alerts")
    ax3.set_xticks(x)
    ax3.set_xticklabels(pats, fontsize=8, color=DIM)
    ax3.set_ylim(0, 115)
    ax3.set_ylabel("Tool Call Success %", color=DIM)
    ax3b.set_ylabel("Alerts", color="#feca57")
    ax3.set_title("Reliability & Alerts", color=TEXT, fontsize=10, pad=8)
    ax3.grid(axis="y", color=GRID, lw=0.7)
    ax3.spines[:].set_color(BORDER)
    ax3.tick_params(colors=DIM)
    ax3b.tick_params(colors="#feca57")

    plt.tight_layout(pad=2.0)
    out = out_dir / "01_manual_vs_mcp.png"
    plt.savefig(out, dpi=150, bbox_inches="tight", facecolor=BG)
    plt.close()
    print(f"OK {out}")


def plot_rbac(out_dir: Path):
    checks = test_scope_violation("PAT-006")
    allowed = sum(1 for v in checks.values() if v.get("success"))
    blocked = sum(1 for v in checks.values() if v.get("rbac_blocked"))

    fig, ax = plt.subplots(figsize=(8, 4))
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(PANEL)
    ax.bar(["Allowed", "Blocked"], [allowed, blocked], color=["#1dd1a1", "#ff6b6b"], alpha=0.85, edgecolor=BORDER)
    ax.set_title("RBAC Enforcement (Scope Tests)", color=TEXT, fontsize=11, pad=10)
    ax.grid(axis="y", color=GRID, lw=0.7)
    ax.spines[:].set_color(BORDER)
    ax.tick_params(colors=DIM)

    out = out_dir / "02_rbac_telemetry.png"
    plt.savefig(out, dpi=150, bbox_inches="tight", facecolor=BG)
    plt.close()
    print(f"OK {out}")


def plot_data_integrity(results: list[dict], out_dir: Path):
    fig, ax = plt.subplots(figsize=(12, 4))
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(PANEL)

    name_mismatch = sum(sum(1 for a in r["result"]["alerts"] if a.get("type") == "NAME_MISMATCH") for r in results)
    out_of_stock = sum(sum(1 for a in r["result"]["alerts"] if a.get("type") == "OUT_OF_STOCK") for r in results)
    dose_conflicts = sum(len(r["result"]["data_integrity_conflicts"]) for r in results)
    phi_blocked = round(sum(len(r["result"]["phi_blocked_fields"]) for r in results) / len(results), 1)

    categories = ["NAME_MISMATCH", "OUT_OF_STOCK", "DOSE_CONFLICTS", "PHI_BLOCKED(avg)"]
    vals = [name_mismatch, out_of_stock, dose_conflicts, phi_blocked]
    colors = ["#48dbfb", "#ff6b6b", "#feca57", "#1dd1a1"]

    ax.bar(range(len(categories)), vals, color=colors, alpha=0.85, edgecolor=BORDER, width=0.55)
    ax.set_xticks(range(len(categories)))
    ax.set_xticklabels(categories, fontsize=9, color=DIM)
    ax.set_title("Safety/Data Integrity Signals Detected", color=TEXT, fontsize=11, pad=10)
    ax.grid(axis="y", color=GRID, lw=0.7)
    ax.spines[:].set_color(BORDER)
    ax.tick_params(colors=DIM)

    out = out_dir / "03_data_integrity.png"
    plt.savefig(out, dpi=150, bbox_inches="tight", facecolor=BG)
    plt.close()
    print(f"OK {out}")


def print_summary(results: list[dict]):
    rows = []
    for r in results:
        res = r["result"]
        rows.append(
            {
                "patient_id": r["patient_id"],
                "mcp_calls": res["mcp_tool_calls_total"],
                "success_rate": res["tool_call_success_rate"],
                "alerts": len(res["alerts"]),
                "subtotal_inr": res["invoice"]["subtotal_inr"],
                "latency_ms": res["total_latency_ms"],
            }
        )
    print(json.dumps({"summary": rows}, indent=2))


if __name__ == "__main__":
    print("Running MCPDischarge evaluation (requires servers running)...")
    results = asyncio.run(run_all())
    print_summary(results)
    print("Generating charts...")
    plot_manual_vs_mcp(results, EVAL_DIR)
    plot_rbac(EVAL_DIR)
    plot_data_integrity(results, EVAL_DIR)
