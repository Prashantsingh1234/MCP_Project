"""
MCPDischarge — Synthetic Dataset Generator
===========================================
Generates mock databases for three MCP servers:
  EHR      ← patient records, discharge notes, medications, ICD codes
  Pharmacy ← drug inventory with stock levels and formulary alternatives
  Billing  ← rate cards, insurance contracts, invoice templates

INJECTED PATTERNS (motivate MCP over traditional APIs):
  [NAME_MISMATCH]    Drug name in EHR vs Pharmacy uses brand vs generic → semantic reconciliation needed
  [OUT_OF_STOCK]     Prescribed drug unavailable → triggers substitution workflow
  [SCOPE_VIOLATION]  Billing agent attempts to read clinical notes → RBAC blocks it
  [DATA_DRIFT]       EHR dose differs from pharmacy formulary standard dose → conflict alert
  [CROSS_DEPT]       Discharge coordination requires EHR + Pharmacy + Billing all in one transaction
  [PHI_BOUNDARY]     ICD-10 + LOS flows to Billing; narrative clinical text MUST NOT
"""

import json
import random
from pathlib import Path
from datetime import datetime, timedelta

random.seed(42)
OUT = Path(__file__).parent


# ─────────────────────────────────────────────────────────────────────────────
# EHR DATABASE — patient records + discharge summaries
# ─────────────────────────────────────────────────────────────────────────────

EHR_PATIENTS = [
    {
        "patient_id": "PAT-001",
        "mrn": "MH-44218",
        "name": "Arjun Mehta",           # PHI — must NOT cross to Billing
        "dob": "1958-06-15",             # PHI
        "blood_group": "B+",
        "ward": "Cardiology",
        "admission_date": "2025-01-14",
        "discharge_date": "2025-01-19",
        "los_days": 5,
        "attending_physician": "Dr. Ananya Sharma",
        "diagnosis_icd10": ["I50.20", "E11.65"],  # shared with Billing (non-PHI)
        "diagnosis_labels": ["HFrEF", "T2DM with CKD"],
        "discharge_note": (                         # PHI — clinical narrative, BLOCKED from Billing
            "Patient Arjun Mehta, 67M, admitted with acute HFrEF decompensation. "
            "LVEF confirmed 32% on echo. HbA1c 8.1%. Creatinine stable at 1.8. "
            "Treated with IV furosemide, bisoprolol uptitrated, dapagliflozin initiated. "
            "Discharged on: dapagliflozin 10mg OD, bisoprolol 5mg OD, ramipril 5mg OD, "
            "furosemide 40mg OD. Weight reduced 3.2kg from admission. "
            "Follow-up heart failure clinic 2 weeks. Fluid restriction 1.5L/day."
        ),
        "discharge_medications": [
            {"drug_name": "Dapagliflozin", "dose": "10mg", "frequency": "OD", "days_supply": 30,
             "route": "oral", "brand": "Farxiga", "challenge_tags": ["NAME_MISMATCH"]},
            {"drug_name": "Bisoprolol fumarate", "dose": "5mg", "frequency": "OD", "days_supply": 30,
             "route": "oral", "brand": "Concor", "challenge_tags": []},
            {"drug_name": "Ramipril", "dose": "5mg", "frequency": "OD", "days_supply": 30,
             "route": "oral", "brand": "Altace", "challenge_tags": []},
            {"drug_name": "Furosemide", "dose": "40mg", "frequency": "OD", "days_supply": 14,
             "route": "oral", "brand": "Lasix", "challenge_tags": ["OUT_OF_STOCK"]},
        ],
        "special_instructions": "Low sodium diet. Fluid restriction 1.5L/day. Daily weight monitoring.",
        "challenge_tags": ["NAME_MISMATCH", "OUT_OF_STOCK", "PHI_BOUNDARY"],
    },
    {
        "patient_id": "PAT-002",
        "mrn": "KA-77329",
        "name": "Fatima Sheikh",
        "dob": "1956-09-22",
        "blood_group": "O+",
        "ward": "Nephrology",
        "admission_date": "2025-01-18",
        "discharge_date": "2025-01-25",
        "los_days": 7,
        "attending_physician": "Dr. Suresh Pillai",
        "diagnosis_icd10": ["N17.9", "E11.65"],
        "diagnosis_labels": ["Acute Kidney Injury", "T2DM with CKD"],
        "discharge_note": (
            "68F admitted with AKI stage 2 superimposed on CKD3b. "
            "Creatinine peak 3.1 on admission, improved to 1.9 at discharge. "
            "IV fluids, nephrotoxic drugs held. Metformin held — eGFR 32. "
            "Discharged on: semaglutide 0.5mg weekly SC, amlodipine 5mg OD, "
            "atorvastatin 40mg nocte. Renal diet. Monthly creatinine monitoring."
        ),
        "discharge_medications": [
            {"drug_name": "Semaglutide", "dose": "0.5mg", "frequency": "weekly", "days_supply": 28,
             "route": "SC", "brand": "Ozempic", "challenge_tags": ["DATA_DRIFT"]},
            {"drug_name": "Amlodipine", "dose": "5mg", "frequency": "OD", "days_supply": 30,
             "route": "oral", "brand": "Norvasc", "challenge_tags": []},
            {"drug_name": "Atorvastatin", "dose": "40mg", "frequency": "nocte", "days_supply": 30,
             "route": "oral", "brand": "Lipitor", "challenge_tags": []},
        ],
        "special_instructions": "Renal diet — potassium restriction. No NSAIDs. eGFR monitoring monthly.",
        "challenge_tags": ["DATA_DRIFT", "PHI_BOUNDARY"],
    },
    {
        "patient_id": "PAT-003",
        "mrn": "TN-22190",
        "name": "Meera Krishnan",
        "dob": "1977-03-08",
        "blood_group": "A+",
        "ward": "Rheumatology",
        "admission_date": "2025-01-22",
        "discharge_date": "2025-01-27",
        "los_days": 5,
        "attending_physician": "Dr. Arun Nair",
        "diagnosis_icd10": ["M05.79"],
        "diagnosis_labels": ["Rheumatoid Arthritis"],
        "discharge_note": (
            "48F with high-activity seropositive RA (DAS28 5.6). "
            "Admitted for initiation and monitoring of adalimumab. "
            "First dose administered in-hospital. Tolerated well. "
            "Discharged on: adalimumab 40mg SC q2w (home delivery arranged), "
            "methotrexate 15mg weekly oral, folic acid 5mg weekly."
        ),
        "discharge_medications": [
            {"drug_name": "Adalimumab", "dose": "40mg", "frequency": "q2w", "days_supply": 28,
             "route": "SC", "brand": "Humira", "challenge_tags": ["OUT_OF_STOCK", "NAME_MISMATCH"]},
            {"drug_name": "Methotrexate", "dose": "15mg", "frequency": "weekly", "days_supply": 28,
             "route": "oral", "brand": None, "challenge_tags": []},
            {"drug_name": "Folic acid", "dose": "5mg", "frequency": "weekly", "days_supply": 28,
             "route": "oral", "brand": None, "challenge_tags": []},
        ],
        "special_instructions": "TB screen confirmed negative. Monitor LFTs, CBC monthly. No live vaccines.",
        "challenge_tags": ["OUT_OF_STOCK", "NAME_MISMATCH", "PHI_BOUNDARY"],
    },
    {
        "patient_id": "PAT-004",
        "mrn": "MH-55432",
        "name": "Vinod Rao",
        "dob": "1952-11-04",
        "blood_group": "B+",
        "ward": "Cardiology",
        "admission_date": "2025-01-20",
        "discharge_date": "2025-01-24",
        "los_days": 4,
        "attending_physician": "Dr. Priya Menon",
        "diagnosis_icd10": ["E85.4", "I50.20"],
        "diagnosis_labels": ["ATTR Amyloidosis", "HFrEF"],
        "discharge_note": (
            "72M with wild-type ATTR cardiac amyloidosis and HFrEF (LVEF 38%). "
            "Technetium PYP grade 3. Tafamidis initiated. "
            "Discharged on: tafamidis 61mg OD, bisoprolol 2.5mg OD, "
            "spironolactone 25mg OD, furosemide 20mg OD."
        ),
        "discharge_medications": [
            {"drug_name": "Tafamidis meglumine", "dose": "61mg", "frequency": "OD", "days_supply": 30,
             "route": "oral", "brand": "Vyndamax", "challenge_tags": ["NAME_MISMATCH", "OUT_OF_STOCK"]},
            {"drug_name": "Bisoprolol fumarate", "dose": "2.5mg", "frequency": "OD", "days_supply": 30,
             "route": "oral", "brand": "Concor", "challenge_tags": []},
            {"drug_name": "Spironolactone", "dose": "25mg", "frequency": "OD", "days_supply": 30,
             "route": "oral", "brand": "Aldactone", "challenge_tags": []},
            {"drug_name": "Furosemide", "dose": "20mg", "frequency": "OD", "days_supply": 14,
             "route": "oral", "brand": "Lasix", "challenge_tags": []},
        ],
        "special_instructions": "Rare disease case. Tafamidis is specialty pharmacy — confirm supply before discharge.",
        "challenge_tags": ["NAME_MISMATCH", "OUT_OF_STOCK", "PHI_BOUNDARY"],
    },
    {
        "patient_id": "PAT-005",
        "mrn": "GJ-88123",
        "name": "Sanjay Patel",
        "dob": "1959-03-12",
        "blood_group": "O+",
        "ward": "Oncology",
        "admission_date": "2025-01-15",
        "discharge_date": "2025-01-19",
        "los_days": 4,
        "attending_physician": "Dr. Ramesh Gupta",
        "diagnosis_icd10": ["C34.10"],
        "diagnosis_labels": ["NSCLC"],
        "discharge_note": (
            "65M NSCLC (EGFR del19). Admitted for osimertinib initiation and monitoring. "
            "Tolerating well. No significant adverse events. "
            "Discharged on: osimertinib 80mg OD. Onco follow-up 4 weeks. "
            "CT chest scheduled 3 months."
        ),
        "discharge_medications": [
            {"drug_name": "Osimertinib", "dose": "80mg", "frequency": "OD", "days_supply": 30,
             "route": "oral", "brand": "Tagrisso", "challenge_tags": ["OUT_OF_STOCK", "CROSS_DEPT"]},
        ],
        "special_instructions": "High-cost specialty drug. Insurance PA confirmed. Pharmacy direct delivery.",
        "challenge_tags": ["OUT_OF_STOCK", "CROSS_DEPT", "PHI_BOUNDARY"],
    },
    {
        "patient_id": "PAT-006",
        "mrn": "MH-33124",
        "name": "Sunita Rao",
        "dob": "1987-07-19",
        "blood_group": "A-",
        "ward": "Neurology",
        "admission_date": "2025-01-21",
        "discharge_date": "2025-01-23",
        "los_days": 2,
        "attending_physician": "Dr. Vikram Desai",
        "diagnosis_icd10": ["G35"],
        "diagnosis_labels": ["Multiple Sclerosis"],
        "discharge_note": (
            "38F RRMS. Admitted for MS relapse — IV methylprednisolone 1g × 3 days completed. "
            "Ocrelizumab scheduled next cycle (outpatient infusion). "
            "Discharged on: baclofen 10mg TDS, modafinil 100mg OD."
        ),
        "discharge_medications": [
            {"drug_name": "Baclofen", "dose": "10mg", "frequency": "TDS", "days_supply": 30,
             "route": "oral", "brand": None, "challenge_tags": []},
            {"drug_name": "Modafinil", "dose": "100mg", "frequency": "OD", "days_supply": 30,
             "route": "oral", "brand": "Provigil", "challenge_tags": ["SCOPE_VIOLATION"]},
        ],
        "special_instructions": "Modafinil — schedule H drug. Patient counselled on side effects. Next ocrelizumab in 6 months.",
        "challenge_tags": ["SCOPE_VIOLATION", "PHI_BOUNDARY"],
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# PHARMACY DATABASE — drug inventory with injected stock issues
# ─────────────────────────────────────────────────────────────────────────────

PHARMACY_INVENTORY = [
    # Cardiovascular
    {
        "drug_id": "PH-001", "generic_name": "Dapagliflozin", "brand_names": ["Farxiga", "Forxiga"],
        "strengths": ["10mg", "5mg"], "formulations": ["tablet"],
        "in_stock": True, "stock_units": 240, "reorder_threshold": 50,
        "price_per_unit_inr": 48.50, "requires_refrigeration": False,
        "formulary_standard_dose": "10mg OD",
        "controlled_substance": False, "specialty_drug": False,
        "semantic_aliases": ["dapa", "SGLT2 inhibitor", "farxiga"],
        "challenge_tags": ["NAME_MISMATCH"],
        "note": "EHR uses brand 'Farxiga'; pharmacy stocks under generic 'Dapagliflozin'"
    },
    {
        "drug_id": "PH-002", "generic_name": "Bisoprolol fumarate", "brand_names": ["Concor", "Zebeta"],
        "strengths": ["2.5mg", "5mg", "10mg"], "formulations": ["tablet"],
        "in_stock": True, "stock_units": 480, "reorder_threshold": 100,
        "price_per_unit_inr": 4.20, "requires_refrigeration": False,
        "formulary_standard_dose": "5mg OD",
        "controlled_substance": False, "specialty_drug": False,
        "semantic_aliases": ["bisoprolol", "beta blocker", "concor"],
        "challenge_tags": []
    },
    {
        "drug_id": "PH-003", "generic_name": "Ramipril", "brand_names": ["Altace", "Cardace"],
        "strengths": ["2.5mg", "5mg", "10mg"], "formulations": ["capsule", "tablet"],
        "in_stock": True, "stock_units": 360, "reorder_threshold": 80,
        "price_per_unit_inr": 3.80, "requires_refrigeration": False,
        "formulary_standard_dose": "5mg OD",
        "controlled_substance": False, "specialty_drug": False,
        "semantic_aliases": ["ramipril", "ACE inhibitor", "altace"],
        "challenge_tags": []
    },
    {
        "drug_id": "PH-004", "generic_name": "Furosemide", "brand_names": ["Lasix", "Frumil"],
        "strengths": ["20mg", "40mg", "80mg"], "formulations": ["tablet"],
        "in_stock": False, "stock_units": 0, "reorder_threshold": 100,
        "restock_eta_days": 3,
        "price_per_unit_inr": 1.20, "requires_refrigeration": False,
        "formulary_standard_dose": "40mg OD",
        "controlled_substance": False, "specialty_drug": False,
        "alternative_drug_id": "PH-020",
        "alternative_name": "Torsemide 10mg (loop diuretic alternative)",
        "semantic_aliases": ["furosemide", "frusemide", "loop diuretic", "lasix"],
        "challenge_tags": ["OUT_OF_STOCK"],
        "note": "OUT OF STOCK — PAT-001 Furosemide 40mg OD cannot be dispensed. Restock in 3 days. Alternative: Torsemide."
    },
    {
        "drug_id": "PH-005", "generic_name": "Semaglutide", "brand_names": ["Ozempic", "Rybelsus"],
        "strengths": ["0.5mg/dose", "1mg/dose"], "formulations": ["prefilled_pen_SC", "tablet"],
        "in_stock": True, "stock_units": 48, "reorder_threshold": 20,
        "price_per_unit_inr": 3200.00, "requires_refrigeration": True,
        "formulary_standard_dose": "0.25mg SC weekly (starter), escalate to 0.5mg",
        "controlled_substance": False, "specialty_drug": True,
        "semantic_aliases": ["semaglutide", "GLP-1 agonist", "ozempic"],
        "challenge_tags": ["DATA_DRIFT"],
        "note": "DOSE DRIFT: EHR prescribes 0.5mg weekly (maintenance dose). Formulary starter is 0.25mg weekly × 4 weeks. Conflict alert required."
    },
    {
        "drug_id": "PH-006", "generic_name": "Amlodipine", "brand_names": ["Norvasc", "Amlong"],
        "strengths": ["5mg", "10mg"], "formulations": ["tablet"],
        "in_stock": True, "stock_units": 600, "reorder_threshold": 100,
        "price_per_unit_inr": 2.50, "requires_refrigeration": False,
        "formulary_standard_dose": "5mg OD",
        "controlled_substance": False, "specialty_drug": False,
        "semantic_aliases": ["amlodipine", "CCB", "calcium channel blocker", "norvasc"],
        "challenge_tags": []
    },
    {
        "drug_id": "PH-007", "generic_name": "Atorvastatin", "brand_names": ["Lipitor", "Atorva"],
        "strengths": ["10mg", "20mg", "40mg", "80mg"], "formulations": ["tablet"],
        "in_stock": True, "stock_units": 720, "reorder_threshold": 100,
        "price_per_unit_inr": 6.40, "requires_refrigeration": False,
        "formulary_standard_dose": "40mg nocte",
        "controlled_substance": False, "specialty_drug": False,
        "semantic_aliases": ["atorvastatin", "statin", "lipitor"],
        "challenge_tags": []
    },
    {
        "drug_id": "PH-008", "generic_name": "Adalimumab", "brand_names": ["Humira", "Exemptia", "Cimzia"],
        "strengths": ["40mg/0.8mL"], "formulations": ["prefilled_syringe_SC"],
        "in_stock": False, "stock_units": 0, "reorder_threshold": 10,
        "restock_eta_days": 7,
        "price_per_unit_inr": 42000.00, "requires_refrigeration": True,
        "formulary_standard_dose": "40mg SC q2w",
        "controlled_substance": False, "specialty_drug": True,
        "alternative_drug_id": "PH-021",
        "alternative_name": "Adalimumab biosimilar (Exemptia 40mg) — 30% lower cost",
        "semantic_aliases": ["adalimumab", "TNF inhibitor", "anti-TNF", "humira", "biologic"],
        "challenge_tags": ["OUT_OF_STOCK", "NAME_MISMATCH"],
        "note": "Branded Humira OUT OF STOCK. Biosimilar Exemptia available. EHR note says 'Humira' — semantic match to adalimumab required."
    },
    {
        "drug_id": "PH-009", "generic_name": "Methotrexate", "brand_names": ["Methofar", "Methocel"],
        "strengths": ["5mg", "10mg", "15mg", "25mg"], "formulations": ["tablet"],
        "in_stock": True, "stock_units": 280, "reorder_threshold": 60,
        "price_per_unit_inr": 8.60, "requires_refrigeration": False,
        "formulary_standard_dose": "15mg weekly",
        "controlled_substance": False, "specialty_drug": False,
        "semantic_aliases": ["methotrexate", "MTX", "DMARD"],
        "challenge_tags": []
    },
    {
        "drug_id": "PH-010", "generic_name": "Folic acid", "brand_names": ["Folvite"],
        "strengths": ["1mg", "5mg"], "formulations": ["tablet"],
        "in_stock": True, "stock_units": 1200, "reorder_threshold": 200,
        "price_per_unit_inr": 0.80, "requires_refrigeration": False,
        "formulary_standard_dose": "5mg weekly (day after MTX)",
        "controlled_substance": False, "specialty_drug": False,
        "semantic_aliases": ["folic acid", "folate", "vitamin B9"],
        "challenge_tags": []
    },
    {
        "drug_id": "PH-011", "generic_name": "Tafamidis", "brand_names": ["Vyndamax", "Vyndaqel"],
        "strengths": ["61mg", "20mg"], "formulations": ["capsule"],
        "in_stock": False, "stock_units": 0, "reorder_threshold": 5,
        "restock_eta_days": 14,
        "price_per_unit_inr": 21000.00, "requires_refrigeration": False,
        "formulary_standard_dose": "61mg OD",
        "controlled_substance": False, "specialty_drug": True,
        "alternative_drug_id": None,
        "alternative_name": "No alternative — disease-specific TTR stabiliser",
        "semantic_aliases": ["tafamidis", "TTR stabiliser", "amyloidosis drug", "vyndamax"],
        "challenge_tags": ["OUT_OF_STOCK", "NAME_MISMATCH"],
        "note": "CRITICAL STOCK-OUT: rare disease drug. EHR uses 'Tafamidis meglumine' — maps to Vyndamax 61mg. No alternative. Must source from specialty pharmacy."
    },
    {
        "drug_id": "PH-012", "generic_name": "Spironolactone", "brand_names": ["Aldactone"],
        "strengths": ["25mg", "50mg", "100mg"], "formulations": ["tablet"],
        "in_stock": True, "stock_units": 400, "reorder_threshold": 80,
        "price_per_unit_inr": 2.10, "requires_refrigeration": False,
        "formulary_standard_dose": "25mg OD",
        "controlled_substance": False, "specialty_drug": False,
        "semantic_aliases": ["spironolactone", "MRA", "aldosterone antagonist"],
        "challenge_tags": []
    },
    {
        "drug_id": "PH-013", "generic_name": "Osimertinib", "brand_names": ["Tagrisso"],
        "strengths": ["80mg", "40mg"], "formulations": ["tablet"],
        "in_stock": False, "stock_units": 0, "reorder_threshold": 5,
        "restock_eta_days": 5,
        "price_per_unit_inr": 18500.00, "requires_refrigeration": False,
        "formulary_standard_dose": "80mg OD",
        "controlled_substance": False, "specialty_drug": True,
        "alternative_drug_id": None,
        "alternative_name": "No generic available — PA authorisation must confirm before dispensing",
        "semantic_aliases": ["osimertinib", "EGFR TKI", "tagrisso", "lung cancer drug"],
        "challenge_tags": ["OUT_OF_STOCK", "CROSS_DEPT"],
        "note": "Specialty high-cost drug. Insurance PA already approved. Central pharmacy order required — ETA 5 days."
    },
    {
        "drug_id": "PH-014", "generic_name": "Baclofen", "brand_names": ["Lioresal"],
        "strengths": ["5mg", "10mg", "25mg"], "formulations": ["tablet"],
        "in_stock": True, "stock_units": 360, "reorder_threshold": 80,
        "price_per_unit_inr": 3.50, "requires_refrigeration": False,
        "formulary_standard_dose": "10mg TDS",
        "controlled_substance": False, "specialty_drug": False,
        "semantic_aliases": ["baclofen", "muscle relaxant", "GABA-B agonist"],
        "challenge_tags": []
    },
    {
        "drug_id": "PH-015", "generic_name": "Modafinil", "brand_names": ["Provigil", "Modalert"],
        "strengths": ["100mg", "200mg"], "formulations": ["tablet"],
        "in_stock": True, "stock_units": 120, "reorder_threshold": 30,
        "price_per_unit_inr": 28.00, "requires_refrigeration": False,
        "formulary_standard_dose": "100mg OD",
        "controlled_substance": True, "schedule": "H",
        "specialty_drug": False,
        "semantic_aliases": ["modafinil", "wakefulness agent", "provigil"],
        "challenge_tags": ["SCOPE_VIOLATION"],
        "note": "Schedule H — requires special handling. Billing agent must NOT see prescription details for controlled substances."
    },
    # Alternatives
    {
        "drug_id": "PH-020", "generic_name": "Torsemide", "brand_names": ["Demadex"],
        "strengths": ["10mg", "20mg"], "formulations": ["tablet"],
        "in_stock": True, "stock_units": 200, "reorder_threshold": 50,
        "price_per_unit_inr": 2.80, "requires_refrigeration": False,
        "formulary_standard_dose": "10mg OD (≡ furosemide 40mg)",
        "controlled_substance": False, "specialty_drug": False,
        "semantic_aliases": ["torsemide", "loop diuretic", "torasemide"],
        "challenge_tags": []
    },
    {
        "drug_id": "PH-021", "generic_name": "Adalimumab biosimilar", "brand_names": ["Exemptia"],
        "strengths": ["40mg/0.8mL"], "formulations": ["prefilled_syringe_SC"],
        "in_stock": True, "stock_units": 18, "reorder_threshold": 10,
        "price_per_unit_inr": 29400.00, "requires_refrigeration": True,
        "formulary_standard_dose": "40mg SC q2w",
        "controlled_substance": False, "specialty_drug": True,
        "semantic_aliases": ["adalimumab biosimilar", "exemptia", "biosimilar anti-TNF"],
        "challenge_tags": []
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# BILLING DATABASE — rate cards, insurance, charge codes
# ─────────────────────────────────────────────────────────────────────────────

BILLING_RATE_CARDS = [
    # Ward rates per day
    {"charge_code": "WRD-CARD", "description": "Cardiology ward — per diem", "rate_inr": 4200, "unit": "per_day"},
    {"charge_code": "WRD-NEPH", "description": "Nephrology ward — per diem", "rate_inr": 3800, "unit": "per_day"},
    {"charge_code": "WRD-RHEU", "description": "Rheumatology ward — per diem", "rate_inr": 3600, "unit": "per_day"},
    {"charge_code": "WRD-ONCO", "description": "Oncology ward — per diem", "rate_inr": 5200, "unit": "per_day"},
    {"charge_code": "WRD-NEUR", "description": "Neurology ward — per diem", "rate_inr": 4000, "unit": "per_day"},
    # Investigations
    {"charge_code": "INV-ECHO", "description": "Echocardiogram", "rate_inr": 3500, "unit": "per_session"},
    {"charge_code": "INV-MRI",  "description": "MRI brain/spine", "rate_inr": 8500, "unit": "per_session"},
    {"charge_code": "INV-CT",   "description": "CT chest", "rate_inr": 4200, "unit": "per_session"},
    {"charge_code": "INV-LAB",  "description": "Standard lab panel", "rate_inr": 1200, "unit": "per_day"},
    {"charge_code": "INV-PYP",  "description": "Technetium PYP scan", "rate_inr": 12000, "unit": "per_session"},
    # Procedures
    {"charge_code": "PRO-IV",   "description": "IV therapy administration", "rate_inr": 800, "unit": "per_day"},
    {"charge_code": "PRO-CONS", "description": "Specialist consultation", "rate_inr": 2500, "unit": "per_session"},
    {"charge_code": "PRO-BIOI", "description": "Biologic drug administration", "rate_inr": 4000, "unit": "per_session"},
    # Drug dispensing
    {"charge_code": "DRG-STD",  "description": "Standard formulary drug dispensing", "rate_inr": 0, "unit": "pass_through"},
    {"charge_code": "DRG-SPEC", "description": "Specialty drug dispensing fee", "rate_inr": 500, "unit": "per_item"},
]

INSURANCE_CONTRACTS = [
    {
        "insurer_id": "INS-BLUE",
        "insurer_name": "BlueStar Health",
        "plan_type": "corporate_group",
        "copay_inr": 500,
        "deductible_inr": 5000,
        "max_covered_per_admission_inr": 300000,
        "covered_icd10_prefixes": ["I", "E", "N", "M", "G", "C"],
        "specialty_drug_covered": True,
        "specialty_drug_copay_pct": 0.10,
        "requires_pa_for_specialty": True,
        "excluded_charges": ["SCOPE_VIOLATION"],  # controlled substance details
    },
    {
        "insurer_id": "INS-STAR",
        "insurer_name": "Star Health",
        "plan_type": "individual",
        "copay_inr": 1000,
        "deductible_inr": 10000,
        "max_covered_per_admission_inr": 150000,
        "covered_icd10_prefixes": ["I", "E", "N", "M", "G"],
        "specialty_drug_covered": False,
        "requires_pa_for_specialty": True,
        "excluded_charges": [],
    },
]

PATIENT_INSURANCE_MAP = {
    "PAT-001": {"insurer_id": "INS-BLUE", "policy_number": "BS-9871234", "pa_required": True},
    "PAT-002": {"insurer_id": "INS-BLUE", "policy_number": "BS-2234567", "pa_required": False},
    "PAT-003": {"insurer_id": "INS-STAR", "policy_number": "SH-3312900", "pa_required": True},
    "PAT-004": {"insurer_id": "INS-BLUE", "policy_number": "BS-9876001", "pa_required": True},
    "PAT-005": {"insurer_id": "INS-BLUE", "policy_number": "BS-5543210", "pa_required": True},
    "PAT-006": {"insurer_id": "INS-STAR", "policy_number": "SH-8877001", "pa_required": False},
}

# ICD-10 to billing mappings (what Billing IS allowed to see — non-PHI)
ICD10_BILLING_CODES = {
    "I50.20": {"description": "Systolic heart failure", "drg_code": "DRG-291", "base_reimbursement_inr": 48000},
    "E11.65": {"description": "T2DM with CKD", "drg_code": "DRG-637", "base_reimbursement_inr": 22000},
    "N17.9":  {"description": "Acute kidney injury", "drg_code": "DRG-682", "base_reimbursement_inr": 35000},
    "M05.79": {"description": "Rheumatoid arthritis", "drg_code": "DRG-545", "base_reimbursement_inr": 28000},
    "E85.4":  {"description": "ATTR amyloidosis", "drg_code": "DRG-642", "base_reimbursement_inr": 42000},
    "I50.20_combined": {"description": "HFrEF + ATTR", "base_reimbursement_inr": 62000},
    "C34.10": {"description": "NSCLC", "drg_code": "DRG-180", "base_reimbursement_inr": 38000},
    "G35":    {"description": "Multiple sclerosis", "drg_code": "DRG-058", "base_reimbursement_inr": 24000},
}

# RBAC — what each role is ALLOWED to access
RBAC_POLICIES = {
    "discharge_coordinator": {
        "ehr": ["read_discharge_note", "read_medications", "read_diagnosis_codes",
                "read_patient_demographics", "read_admission_dates"],
        "pharmacy": ["check_stock", "get_alternatives", "get_drug_price",
                     "submit_dispense_request"],
        "billing": ["read_charge_codes", "generate_invoice", "read_insurance_contract"],
    },
    "billing_agent": {
        "ehr": ["read_diagnosis_codes", "read_admission_dates", "read_ward"],  # NO clinical notes, NO drug list
        "pharmacy": ["read_drug_price"],  # drug cost only, not clinical details
        "billing": ["read_charge_codes", "generate_invoice", "read_insurance_contract",
                    "submit_claim"],
    },
    "pharmacy_agent": {
        "ehr": ["read_medications", "read_diagnosis_codes"],  # NO full discharge note
        "pharmacy": ["check_stock", "get_alternatives", "get_drug_price",
                     "submit_dispense_request", "update_inventory"],
        "billing": [],  # pharmacy cannot touch billing
    },
    "clinical_agent": {
        "ehr": ["read_discharge_note", "read_medications", "read_diagnosis_codes",
                "read_patient_demographics", "read_admission_dates", "update_discharge_note"],
        "pharmacy": ["check_stock"],
        "billing": [],  # clinical agent cannot touch billing
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# WRITE ALL FILES
# ─────────────────────────────────────────────────────────────────────────────

def write_all():
    datasets = {
        "ehr_patients.json": EHR_PATIENTS,
        "pharmacy_inventory.json": PHARMACY_INVENTORY,
        "billing_rate_cards.json": BILLING_RATE_CARDS,
        "insurance_contracts.json": INSURANCE_CONTRACTS,
        "patient_insurance_map.json": PATIENT_INSURANCE_MAP,
        "icd10_billing_codes.json": ICD10_BILLING_CODES,
        "rbac_policies.json": RBAC_POLICIES,
    }
    for fname, data in datasets.items():
        with open(OUT / fname, "w") as f:
            json.dump(data, f, indent=2)
        n = len(data) if isinstance(data, list) else len(data) if isinstance(data, dict) else "?"
        print(f"  ✓ {fname} — {n}")

    from collections import Counter
    print(f"\n{'='*55}\nDATASET SUMMARY\n{'='*55}")
    print(f"  EHR patients              : {len(EHR_PATIENTS)}")
    print(f"  Pharmacy inventory items  : {len(PHARMACY_INVENTORY)}")
    print(f"  Billing rate codes        : {len(BILLING_RATE_CARDS)}")
    print(f"  Insurance contracts       : {len(INSURANCE_CONTRACTS)}")
    out_of_stock = sum(1 for d in PHARMACY_INVENTORY if not d["in_stock"])
    print(f"  Out-of-stock drugs        : {out_of_stock}")
    tags = []
    for p in EHR_PATIENTS:
        tags.extend(p.get("challenge_tags", []))
    print("\n  Challenge tag distribution:")
    for t, c in Counter(tags).most_common():
        print(f"    [{t}]: {c} patients")

if __name__ == "__main__":
    write_all()
