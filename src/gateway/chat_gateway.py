"""Chat gateway API for MCPDischarge.

This is a lightweight HTTP API that a browser-based frontend can call.
It routes user questions to the underlying MCP servers (EHR/Pharmacy/Billing)
via the async MCP client and/or the DischargeCoordinationAgent.

Run:
  python -m uvicorn src.gateway.chat_gateway:app --reload --port 8000
"""

from __future__ import annotations

import re
import time
import logging
from typing import Any, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from src.agents.discharge_agent import AsyncMCPToolClient, DischargeCoordinationAgent
from src.gateway.llm_azure import chat_completion as azure_chat_completion, is_configured as azure_configured

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("MCPDischargeChatGateway")


PATIENT_ID_RE = re.compile(r"\bPAT-\d{3}\b", re.IGNORECASE)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    role: str = Field(default="discharge_coordinator")


class ChatResponse(BaseModel):
    answer: str
    data: Optional[dict[str, Any]] = None
    latency_ms: float


app = FastAPI(title="MCPDischarge Chat Gateway", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _extract_patient_id(message: str) -> Optional[str]:
    match = PATIENT_ID_RE.search(message)
    return match.group(0).upper() if match else None


def _intent(message: str) -> str:
    m = message.lower()
    if any(k in m for k in ["invoice", "bill", "charges", "payment"]):
        return "invoice"
    if any(k in m for k in ["billing safe", "billing-safe", "phi stripped", "phi-stripped"]):
        return "billing_safe"
    if any(k in m for k in ["diagnosis", "icd", "icd10"]):
        return "diagnosis"
    if any(k in m for k in ["admission", "los", "length of stay", "ward"]):
        return "admission"
    if any(k in m for k in ["med", "medication", "prescription", "discharge meds"]):
        return "meds"
    if any(k in m for k in ["stock", "available", "availability"]):
        return "stock"
    if any(k in m for k in ["alternative", "substitute", "substitution"]):
        return "alternative"
    if any(k in m for k in ["price", "cost"]):
        return "price"
    return "help"

SYSTEM_PROMPT = """You are MCPDischarge Assistant.
You must follow these safety rules:
- Never request or reveal PHI: name, dob, mrn, discharge_note, attending_physician.
- For patient-specific questions, require a patient id like PAT-001.
- If asked for PHI, refuse and offer billing-safe summary or operational info.

You can answer general questions about how to use the system, what tools do, and what commands to run.
"""


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    start = time.perf_counter()
    message = req.message.strip()
    role = req.role.strip() or "discharge_coordinator"

    patient_id = _extract_patient_id(message)
    intent = _intent(message)

    try:
        if intent == "help":
            if azure_configured():
                answer = await azure_chat_completion(message, SYSTEM_PROMPT)
            else:
                answer = (
                    "Ask about a patient (e.g. PAT-001). Examples:\n"
                    "- \"Show discharge medications for PAT-001\"\n"
                    "- \"Generate invoice for PAT-001\"\n"
                    "- \"Get billing-safe summary for PAT-006\"\n"
                    "- \"Check stock for Farxiga\"\n\n"
                    "To enable natural-language answers, configure Azure OpenAI in .env (see .env.example)."
                )
            latency_ms = (time.perf_counter() - start) * 1000
            return ChatResponse(answer=answer, data=None, latency_ms=round(latency_ms, 1))

        if intent in {"meds", "diagnosis", "admission", "billing_safe", "invoice"} and not patient_id:
            latency_ms = (time.perf_counter() - start) * 1000
            return ChatResponse(
                answer="Include a patient id like PAT-001 in your question.",
                data={"expected": "PAT-###"},
                latency_ms=round(latency_ms, 1),
            )

        if intent == "invoice":
            agent = DischargeCoordinationAgent(role=role)
            result = await agent.orchestrate_discharge(patient_id)  # type: ignore[arg-type]
            invoice = result.get("invoice", {})
            answer = (
                f"Invoice generated for {patient_id}.\n"
                f"- Subtotal (INR): {invoice.get('subtotal_inr')}\n"
                f"- Patient liability (INR): {invoice.get('patient_liability_inr')}\n"
                f"- Status: {invoice.get('status')}"
            )
            latency_ms = (time.perf_counter() - start) * 1000
            return ChatResponse(answer=answer, data={"result": result}, latency_ms=round(latency_ms, 1))

        if intent == "meds":
            async with AsyncMCPToolClient("http://localhost:8001/sse") as ehr:
                meds = await ehr.call_tool("get_discharge_medications", {"patient_id": patient_id, "role": role})
            answer = f"Discharge medications for {patient_id}: {len(meds)} item(s)."
            latency_ms = (time.perf_counter() - start) * 1000
            return ChatResponse(answer=answer, data={"patient_id": patient_id, "medications": meds}, latency_ms=round(latency_ms, 1))

        if intent == "diagnosis":
            async with AsyncMCPToolClient("http://localhost:8001/sse") as ehr:
                dx = await ehr.call_tool("get_diagnosis_codes", {"patient_id": patient_id, "role": role})
            answer = f"Diagnosis codes for {patient_id}: {', '.join(dx.get('diagnosis_icd10', []))}"
            latency_ms = (time.perf_counter() - start) * 1000
            return ChatResponse(answer=answer, data={"diagnosis": dx}, latency_ms=round(latency_ms, 1))

        if intent == "admission":
            async with AsyncMCPToolClient("http://localhost:8001/sse") as ehr:
                info = await ehr.call_tool("get_admission_info", {"patient_id": patient_id, "role": role})
            answer = f"Admission info for {patient_id}: ward={info.get('ward')}, LOS={info.get('los_days')} days."
            latency_ms = (time.perf_counter() - start) * 1000
            return ChatResponse(answer=answer, data={"admission": info}, latency_ms=round(latency_ms, 1))

        if intent == "billing_safe":
            async with AsyncMCPToolClient("http://localhost:8001/sse") as ehr:
                safe = await ehr.call_tool("get_billing_safe_summary", {"patient_id": patient_id, "role": role})
            answer = f"Billing-safe summary for {patient_id} (PHI stripped)."
            latency_ms = (time.perf_counter() - start) * 1000
            return ChatResponse(answer=answer, data={"billing_safe": safe}, latency_ms=round(latency_ms, 1))

        # Drug-focused intents (no patient required)
        if intent in {"stock", "alternative", "price"}:
            drug = message
            # naive extraction: anything after "for"
            if " for " in message.lower():
                drug = message.split(" for ", 1)[1].strip()
            drug = re.sub(r"\b(stock|check|available|availability|alternative|substitute|price|cost)\b", "", drug, flags=re.I).strip(" .,:;")
            if not drug:
                latency_ms = (time.perf_counter() - start) * 1000
                return ChatResponse(answer="Include a drug name in your question.", data=None, latency_ms=round(latency_ms, 1))

            async with AsyncMCPToolClient("http://localhost:8002/sse") as pharmacy:
                if intent == "stock":
                    data = await pharmacy.call_tool("check_stock", {"drug_name": drug, "quantity": 1, "dose": None, "role": role})
                    answer = f"Stock check for {drug}: available={data.get('available')} (units={data.get('stock_units')})."
                elif intent == "alternative":
                    data = await pharmacy.call_tool("get_alternative", {"drug_name": drug, "role": role})
                    count = data.get("count", 0)
                    answer = f"Alternatives for {drug}: {count} option(s)."
                else:
                    data = await pharmacy.call_tool("get_price", {"drug_name": drug, "quantity": 1, "role": role})
                    answer = f"Price for {drug}: unit_price_inr={data.get('unit_price_inr')}."

            latency_ms = (time.perf_counter() - start) * 1000
            return ChatResponse(answer=answer, data={"drug": drug, "result": data}, latency_ms=round(latency_ms, 1))

        latency_ms = (time.perf_counter() - start) * 1000
        if azure_configured():
            answer = await azure_chat_completion(message, SYSTEM_PROMPT)
            return ChatResponse(answer=answer, data={"intent": intent}, latency_ms=round(latency_ms, 1))

        return ChatResponse(
            answer=(
                "I couldn't map that to a tool. Try: meds / invoice / billing-safe / diagnosis / admission / stock / alternative / price.\n"
                "Or configure Azure OpenAI in .env to answer general questions."
            ),
            data={"intent": intent},
            latency_ms=round(latency_ms, 1),
        )

    except Exception as exc:
        logger.exception("Chat request failed")
        latency_ms = (time.perf_counter() - start) * 1000
        return ChatResponse(answer=f"Error: {exc}", data={"intent": intent, "patient_id": patient_id}, latency_ms=round(latency_ms, 1))
