# RBAC Design Guide
====================

## Tool-Level RBAC Pattern
Every MCP tool call validates the caller's role before executing:
```python
def rbac_check(server: str, tool: str, caller_role: str, patient_id: str = ""):
    role_perms = RBAC_POLICIES.get(caller_role, {})
    server_perms = role_perms.get(server, [])
    if tool not in server_perms:
        raise RBACError(f"Role '{caller_role}' cannot call '{tool}' on '{server}'")
```

## PHI Boundary at Tool Level
```python
# EHR Server — billing-safe tool always strips PHI before returning
def get_billing_safe_summary(patient_id, caller_role):
    rbac_check("ehr", "read_diagnosis_codes", caller_role, patient_id)
    patient = EHR_DB[patient_id]
    return {k: v for k, v in patient.items() if k not in PHI_FIELDS}

# Billing Server — validates PHI is absent from incoming payload
def generate_invoice(patient_id, billing_safe_ehr, ...):
    for phi_field in PHI_FIELDS:
        if phi_field in billing_safe_ehr:
            raise PermissionError(f"PHI field '{phi_field}' in billing payload")
```

## Adding a New Role
Add to rbac_policies.json:
```json
{
  "new_role": {
    "ehr": ["read_diagnosis_codes"],
    "pharmacy": [],
    "billing": ["read_charge_codes"]
  }
}
```
No code changes required — RBAC is data-driven.
