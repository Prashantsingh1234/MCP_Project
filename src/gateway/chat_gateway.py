"""Chat gateway API for MCPDischarge.

This is a lightweight HTTP API that a browser-based frontend can call.
It routes user questions to the underlying MCP servers (EHR/Pharmacy/Billing)
via the async MCP client and/or the DischargeCoordinationAgent.

Run:
  python -m uvicorn src.gateway.chat_gateway:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import time
import logging
import socket
import re
from collections import OrderedDict
from typing import Any, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field

from src.chatbot.llm_controller import LLMChatController
from src.chatbot.mcp_client import MCPClient, MCPServerURLs
from src.chatbot.metrics import RequestMetrics
from src.chatbot.rbac_guard import ActorContext
from src.gateway.llm_azure import is_configured as azure_configured
from src.utils.telemetry import get_telemetry
from src.agents.discharge_agent import AsyncMCPToolClient
from src.gateway.invoice_pdf import InvoiceLineItem, build_invoice_data, generate_invoice_pdf, render_invoice_html

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("MCPDischargeChatGateway")

CONTROLLER = LLMChatController()

# ── In-memory session store ────────────────────────────────────────────────────
MAX_SESSIONS = 50
# session_id → {"id", "title", "messages", "created_at", "last_used"}
SESSION_STORE: OrderedDict[str, dict] = OrderedDict()


def _upsert_session(
    conv_id: str,
    user_text: str,
    answer: str,
    data: Any,
    latency_ms: float,
) -> None:
    """Save a completed exchange to the session store."""
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if conv_id not in SESSION_STORE:
        title = (user_text[:50] + "…") if len(user_text) > 50 else user_text
        SESSION_STORE[conv_id] = {
            "id": conv_id,
            "title": title,
            "messages": [],
            "created_at": now,
            "last_used": now,
        }
    else:
        SESSION_STORE.move_to_end(conv_id)
        SESSION_STORE[conv_id]["last_used"] = now

    sess = SESSION_STORE[conv_id]
    ts = time.time() * 1000
    sess["messages"].append({"role": "user", "text": user_text, "ts": ts})
    sess["messages"].append(
        {"role": "assistant", "text": answer, "ts": ts + latency_ms, "latencyMs": latency_ms, "data": data}
    )
    # Keep messages bounded
    if len(sess["messages"]) > 400:
        sess["messages"] = sess["messages"][-400:]

    # Evict oldest sessions beyond the cap
    while len(SESSION_STORE) > MAX_SESSIONS:
        SESSION_STORE.popitem(last=False)


# ── Invoice helpers ────────────────────────────────────────────────────────────

async def _collect_invoice_data(pid: str) -> dict:
    """Collect all data required to render an invoice via MCP SSE.

    Runs inside a fresh event loop (spawned from asyncio.to_thread) so that the
    MCP SDK's anyio cancel scopes never conflict with FastAPI's event loop.
    """
    actor = ActorContext(role="discharge_coordinator")
    metrics_obj = RequestMetrics(patient_id=pid)
    urls = MCPServerURLs()

    async with MCPClient(urls, actor, metrics_obj) as client:
        billing_safe = await client.ehr_call(
            "get_billing_safe_summary", {"patient_id": pid}, patient_id=pid
        )
        meds = await client.ehr_call(
            "get_discharge_medications", {"patient_id": pid}, patient_id=pid
        )
        charges = await client.billing_call(
            "get_charges",
            {"ward": billing_safe.get("ward"), "los_days": int(billing_safe.get("los_days", 1))},
            patient_id=pid,
        )

        line_items: list[InvoiceLineItem] = [
            InvoiceLineItem(
                code=str(charges.get("ward_charge_code") or ""),
                name="Ward charges",
                description=f"{charges.get('ward')} ward stay",
                quantity=int(charges.get("los_days", 1)),
                unit_price_inr=float(charges.get("ward_rate_per_day", 0) or 0),
                total_price_inr=float(charges.get("ward_total", 0) or 0),
            ),
            InvoiceLineItem(
                code=str(charges.get("lab_charge_code") or ""),
                name="Lab charges",
                description="Standard inpatient investigations (per day)",
                quantity=int(charges.get("los_days", 1)),
                unit_price_inr=float(charges.get("lab_rate_per_day", 0) or 0),
                total_price_inr=float(charges.get("lab_total", 0) or 0),
            ),
        ]

        drug_charges_for_billing: list[dict] = []
        for med in meds:
            drug_label = med.get("drug_name") or med.get("brand") or "Medication"
            desc = " ".join(
                x
                for x in [
                    med.get("dose"),
                    med.get("frequency"),
                    f"{med.get('days_supply')} days" if med.get("days_supply") else None,
                    med.get("route"),
                ]
                if x
            )
            qty = int(med.get("days_supply", 1))
            try:
                price = await client.pharmacy_call(
                    "get_price",
                    {"drug_name": med.get("brand") or med.get("drug_name"), "quantity": qty},
                    patient_id=pid,
                )
            except Exception:
                price = {"unit_price_inr": 0, "total_price_inr": 0, "dispensing_fee": 0}

            unit_price = float(price.get("unit_price_inr", 0) or 0)
            total_price = float(price.get("total_price_inr", 0) or 0)
            fee = float(price.get("dispensing_fee", 0) or 0)

            line_items.append(
                InvoiceLineItem(
                    code="DRG-STD",
                    name=str(drug_label),
                    description=desc or "—",
                    quantity=qty,
                    unit_price_inr=unit_price,
                    total_price_inr=total_price,
                )
            )
            if fee:
                line_items.append(
                    InvoiceLineItem(
                        code="DRG-SPEC",
                        name="Dispensing fee",
                        description=f"Pharmacy dispensing fee for {drug_label}",
                        quantity=1,
                        unit_price_inr=fee,
                        total_price_inr=fee,
                    )
                )
            drug_charges_for_billing.append({"total_price_inr": total_price, "dispensing_fee": fee})

        insurance = await client.billing_call(
            "get_insurance",
            {"patient_id": pid},
            patient_id=pid,
        )

        invoice = await client.billing_call(
            "generate_invoice",
            {"patient_id": pid, "billing_safe_ehr": billing_safe, "drug_charges": drug_charges_for_billing},
            patient_id=pid,
        )

    return {"billing_safe": billing_safe, "insurance": insurance, "invoice": invoice, "line_items": line_items}


def _sync_collect_invoice_data(pid: str) -> dict:
    """Synchronous wrapper: runs the async collection in a brand-new event loop.

    This isolation prevents anyio cancel-scope conflicts with FastAPI's loop.
    """
    return asyncio.run(_collect_invoice_data(pid))


# ── FastAPI app ────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    role: Optional[str] = None
    conversation_id: Optional[str] = None


class ChatResponse(BaseModel):
    answer: str
    data: Optional[dict[str, Any]] = None
    latency_ms: float
    conversation_id: Optional[str] = None


app = FastAPI(title="MCPDischarge Chat Gateway", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz")
def healthz():
    return {"ok": True, "azure_openai_configured": azure_configured()}


def _tcp_ok(host: str, port: int, timeout_s: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def _require_mcp_services() -> Optional[Response]:
    missing: list[str] = []
    if not _tcp_ok("127.0.0.1", 8001):
        missing.append("EHR (8001)")
    if not _tcp_ok("127.0.0.1", 8002):
        missing.append("Pharmacy (8002)")
    if not _tcp_ok("127.0.0.1", 8003):
        missing.append("Billing (8003)")
    if not missing:
        return None
    msg = (
        "Invoice generation failed: required services are not reachable.\n\n"
        f"Missing: {', '.join(missing)}\n\n"
        "Start them with:\n"
        "  python src/servers/mcp_servers.py --all"
    )
    return Response(content=msg, status_code=503, media_type="text/plain")


@app.get("/api/status")
def status():
    return {
        "ehr": {"connected": _tcp_ok("127.0.0.1", 8001)},
        "pharmacy": {"connected": _tcp_ok("127.0.0.1", 8002)},
        "billing": {"connected": _tcp_ok("127.0.0.1", 8003)},
        "azure_openai_configured": azure_configured(),
    }


async def _fetch_remote_telemetry_summary() -> Optional[dict[str, Any]]:
    try:
        async with AsyncMCPToolClient("http://localhost:8005/sse") as c:
            return await asyncio.wait_for(c.call_tool("get_summary", {}), timeout=2.0)
    except Exception:
        return None


async def _fetch_remote_telemetry_logs(limit: int) -> Optional[dict[str, Any]]:
    try:
        async with AsyncMCPToolClient("http://localhost:8005/sse") as c:
            return await asyncio.wait_for(c.call_tool("get_recent_calls", {"limit": int(limit)}), timeout=2.5)
    except Exception:
        return None


@app.get("/api/metrics")
async def metrics():
    remote = await _fetch_remote_telemetry_summary()
    if isinstance(remote, dict) and remote:
        return remote
    return get_telemetry().get_summary()


@app.get("/api/logs")
async def logs(limit: int = 100):
    limit = int(limit or 100)
    # Tool-call logs should come from the telemetry server (single source of truth across servers).
    remote = await _fetch_remote_telemetry_logs(limit)

    # Chat traces are recorded in the gateway process only.
    local_telem = get_telemetry()
    chat_rows = [c.__dict__ for c in local_telem.get_chat_traces(limit=limit)]

    if isinstance(remote, dict) and remote.get("summary") is not None:
        return {
            "summary": remote.get("summary"),
            "chat": chat_rows,
            "calls": remote.get("calls") or [],
            "rbac_violations": remote.get("rbac_violations") or [],
            "alerts": remote.get("alerts") or [],
        }

    # Fallback to local-only telemetry if telemetry server is unavailable.
    calls = local_telem.get_calls(limit=limit)
    return {
        "summary": local_telem.get_summary(),
        "chat": chat_rows,
        "calls": [c.__dict__ for c in calls],
        "rbac_violations": local_telem.get_rbac_violations()[-limit:],
        "alerts": [a.__dict__ for a in local_telem.get_alerts()][-limit:],
    }


# ── Session endpoints ──────────────────────────────────────────────────────────

@app.get("/api/sessions")
def list_sessions():
    sessions = list(SESSION_STORE.values())
    sessions.sort(key=lambda s: s["last_used"], reverse=True)
    return {
        "sessions": [
            {
                "id": s["id"],
                "title": s["title"],
                "created_at": s["created_at"],
                "last_used": s["last_used"],
                "message_count": len(s["messages"]),
            }
            for s in sessions
        ]
    }


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str):
    sess = SESSION_STORE.get(session_id)
    if not sess:
        return Response(content="Session not found", status_code=404, media_type="text/plain")
    return sess


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str):
    SESSION_STORE.pop(session_id, None)
    return {"ok": True}


# ── Invoice endpoints (cancel-scope-safe) ──────────────────────────────────────

@app.get("/api/invoice/pdf")
async def invoice_pdf(patient_id: str):
    pid = (patient_id or "").strip().upper()
    if not pid.startswith("PAT-"):
        return Response(content="Invalid patient_id. Use PAT-XXX.", status_code=400, media_type="text/plain")

    missing = _require_mcp_services()
    if missing is not None:
        return missing

    try:
        # Run MCP calls in a dedicated thread+event-loop to avoid anyio cancel-scope conflicts
        collected = await asyncio.to_thread(_sync_collect_invoice_data, pid)
        payload = build_invoice_data(
            billing_safe_summary=collected["billing_safe"],
            insurance=collected.get("insurance") or {},
            invoice=collected["invoice"],
            line_items=collected["line_items"],
        )
        pdf_bytes = generate_invoice_pdf(payload)
    except RuntimeError as exc:
        return Response(content=str(exc), status_code=500, media_type="text/plain")
    except Exception as exc:
        logger.exception("Invoice PDF generation failed")
        return Response(
            content=f"Invoice generation failed: {exc}",
            status_code=503,
            media_type="text/plain",
        )

    filename = f"invoice_{pid}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/invoice/html")
async def invoice_html(patient_id: str):
    """HTML preview for the invoice, A4-print CSS inlined."""
    pid = (patient_id or "").strip().upper()
    if not pid.startswith("PAT-"):
        return Response(content="Invalid patient_id. Use PAT-XXX.", status_code=400, media_type="text/plain")

    missing = _require_mcp_services()
    if missing is not None:
        return missing

    try:
        collected = await asyncio.to_thread(_sync_collect_invoice_data, pid)
        payload = build_invoice_data(
            billing_safe_summary=collected["billing_safe"],
            insurance=collected.get("insurance") or {},
            invoice=collected["invoice"],
            line_items=collected["line_items"],
        )
        html = render_invoice_html(payload)
    except RuntimeError as exc:
        return Response(content=str(exc), status_code=500, media_type="text/plain")
    except Exception as exc:
        logger.exception("Invoice HTML generation failed")
        return Response(
            content=f"Invoice generation failed: {exc}",
            status_code=503,
            media_type="text/plain",
        )

    return Response(content=html.encode("utf-8"), media_type="text/html; charset=utf-8")


# ── Chat endpoint ──────────────────────────────────────────────────────────────

@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    start = time.perf_counter()
    message = req.message.strip()
    conversation_id = (req.conversation_id or "").strip() or None
    role = (req.role or "").strip() or None

    try:
        ctrl = await CONTROLLER.handle_message(message, conversation_id=conversation_id)

        latency_ms = (time.perf_counter() - start) * 1000

        # Always record a chat trace so the Logs page shows every request (success or fail),
        # even when no MCP tool calls occurred. Avoid logging raw user text (PHI risk).
        try:
            conv_key = conversation_id or "default"
            state = CONTROLLER.conversations.get(conv_key)
            last_mcp = int(getattr(state, "mcp_call_count_last", 0) or 0)
            last_rbac = int(getattr(state, "rbac_violations_last", 0) or 0)

            pid = None
            needs_clarification = False
            clarification_type = None
            if ctrl.data and isinstance(ctrl.data, dict):
                pid = ctrl.data.get("patient_id")
                needs_clarification = bool(ctrl.data.get("needs_clarification") or False)
                clarification_type = ctrl.data.get("clarification_type")
            if not pid:
                m = re.search(r"\bPAT-\d{3}\b", message.upper())
                pid = m.group(0) if m else None

            ok = bool(getattr(ctrl, "success", True))
            err = None if ok else (ctrl.answer or "").strip()
            get_telemetry().record_chat_trace(
                conversation_id=conversation_id,
                role=role,
                patient_id=pid,
                latency_ms=round(latency_ms, 1),
                success=ok,
                mcp_calls=last_mcp,
                rbac_violations=last_rbac,
                needs_clarification=needs_clarification,
                clarification_type=str(clarification_type) if clarification_type else None,
                error=err,
            )
        except Exception:
            pass

        # Record chat-level failures as telemetry alerts so Metrics/Logs reflect user-visible issues
        # even when no MCP tool calls occurred (e.g., LLM timeouts, validation errors).
        if not getattr(ctrl, "success", True):
            try:
                reason = (ctrl.answer or "").strip()
                if len(reason) > 500:
                    reason = reason[:500] + "…"
                get_telemetry().record_alert(
                    "ERROR",
                    "Chat",
                    "Chat request failed",
                    {"conversation_id": conversation_id, "reason": reason},
                )
            except Exception:
                pass

        extra: dict[str, Any] = {}
        if ctrl.data and isinstance(ctrl.data, dict):
            pid = ctrl.data.get("patient_id")
            inv = ctrl.data.get("invoice")
            if pid and inv:
                extra["invoice_pdf_url"] = f"/api/invoice/pdf?patient_id={pid}"
                extra["invoice_html_url"] = f"/api/invoice/html?patient_id={pid}"

            # Multi-patient responses: provide per-patient invoice links so the UI can render them.
            pids = ctrl.data.get("patients")
            if not isinstance(pids, list):
                # Derive patient IDs from multi-patient payloads if the LLM forgot to include `patients`.
                if isinstance(ctrl.data.get("invoices_by_patient"), dict):
                    pids = list(ctrl.data.get("invoices_by_patient").keys())
                elif isinstance(ctrl.data.get("reports_by_patient"), dict):
                    pids = list(ctrl.data.get("reports_by_patient").keys())
            if isinstance(pids, list) and pids:
                try:
                    invoice_pdf_urls = {str(p): f"/api/invoice/pdf?patient_id={p}" for p in pids}
                    invoice_html_urls = {str(p): f"/api/invoice/html?patient_id={p}" for p in pids}
                    extra["invoice_pdf_urls"] = invoice_pdf_urls
                    extra["invoice_html_urls"] = invoice_html_urls
                except Exception:
                    pass

        merged_data: dict[str, Any] = {
            **(ctrl.data or {}),
            **extra,
            "azure_openai_configured": azure_configured(),
        }

        # Save to session store
        if conversation_id:
            _upsert_session(
                conversation_id,
                message,
                ctrl.answer,
                {k: v for k, v in merged_data.items() if k != "azure_openai_configured"},
                round(latency_ms, 1),
            )

        return ChatResponse(
            answer=ctrl.answer,
            data=merged_data,
            latency_ms=round(latency_ms, 1),
            conversation_id=conversation_id,
        )

    except Exception as exc:
        logger.exception("Chat request failed")
        latency_ms = (time.perf_counter() - start) * 1000
        try:
            conv_key = conversation_id or "default"
            state = CONTROLLER.conversations.get(conv_key)
            last_mcp = int(getattr(state, "mcp_call_count_last", 0) or 0)
            last_rbac = int(getattr(state, "rbac_violations_last", 0) or 0)
            m = re.search(r"\bPAT-\d{3}\b", message.upper())
            pid = m.group(0) if m else None
            get_telemetry().record_chat_trace(
                conversation_id=conversation_id,
                role=role,
                patient_id=pid,
                latency_ms=round(latency_ms, 1),
                success=False,
                mcp_calls=last_mcp,
                rbac_violations=last_rbac,
                needs_clarification=False,
                clarification_type=None,
                error=str(exc),
            )
        except Exception:
            pass
        try:
            err = str(exc)
            if len(err) > 500:
                err = err[:500] + "…"
            get_telemetry().record_alert(
                "CRITICAL",
                "Chat",
                "Unhandled exception in /api/chat",
                {"conversation_id": conversation_id, "error": err},
            )
        except Exception:
            pass
        return ChatResponse(
            answer=f"Error: {exc}",
            data={"azure_openai_configured": azure_configured()},
            latency_ms=round(latency_ms, 1),
            conversation_id=conversation_id,
        )
