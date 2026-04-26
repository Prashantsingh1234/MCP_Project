"""Generate a print-ready invoice PDF for a patient.

Prereq: MCP servers + gateway running:
  python src/servers/mcp_servers.py --all
  python -m uvicorn src.gateway.chat_gateway:app --reload --port 8000

Then:
  python demo/generate_invoice_pdf.py PAT-001
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx


def main():
    patient_id = (sys.argv[1] if len(sys.argv) > 1 else "PAT-001").strip().upper()
    out = Path(__file__).parent / f"invoice_{patient_id}.pdf"
    url = "http://localhost:8000/api/invoice/pdf"

    r = httpx.get(url, params={"patient_id": patient_id}, timeout=60)
    if r.status_code >= 400:
        print(f"Request failed ({r.status_code}). Response:\n{r.text}")
        raise SystemExit(1)
    out.write_bytes(r.content)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
