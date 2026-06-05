# Risk Register Schema Documentation

The risk register at `configs/risk_register.json` defines a set of organizational risks mapped to external compliance frameworks. Each entry is a risk that may be cited by labels and decisions within the labeling framework.

## Schema

### Entry Structure

```json
{
  "id": "RISK-PII-DISCLOSURE",
  "summary": "Unintended disclosure of personally identifiable information.",
  "framework_refs": [
    "NIST-CSF-PR.DS-5",
    "ISO-27001-A.8.10"
  ],
  "threshold": {
    "framework": "NIST-AI-RMF",
    "impact_tier_min": "tier-2"
  }
}
```

### Fields

- **id** (string, required): Stable internal identifier for the risk. Slug format: `RISK-{NAME}`. Must be unique within the register.
- **summary** (string, required): Human-readable description of the risk. Used in documentation and audit logs.
- **framework_refs** (array of strings, required): External framework references this risk maps to. Must include at least one reference (CI-lint SC-001). Examples: `NIST-CSF-PR.DS-5`, `ISO-27001-A.8.10`, `OWASP-API3:2023`, `PCI-DSS-3.3`, `HIPAA-164.502`.
- **threshold** (object, optional): Residual-risk threshold for this entry (FR-016). If omitted, the entry can still be cited by labels and decisions, but no residual-risk exception will be emitted when decided (the risk is tracked but not monitored for crossing).

### Threshold Schemas

The `threshold` field shape depends on the framework reference cited:

#### NIST AI RMF (NIST-AI-RMF)
```json
{
  "framework": "NIST-AI-RMF",
  "impact_tier_min": "tier-1" | "tier-2" | "tier-3" | "tier-4"
}
```
Impact tier represents the minimum severity at which this risk crosses its threshold. Decision-time residuals meeting or exceeding this tier trigger an exception.

#### FAIR Framework (FAIR)
```json
{
  "framework": "FAIR",
  "magnitude_band_min": "M1" | "M2" | "M3" | "M4" | "M5"
}
```
Magnitude band represents the minimum loss magnitude at which this risk crosses its threshold (M1=smallest, M5=largest).

#### EU AI Act (EU-AI-Act)
```json
{
  "framework": "EU-AI-Act",
  "risk_class_min": "prohibited" | "high" | "limited" | "minimal"
}
```
Risk class represents the minimum classification at which this risk crosses its threshold.

#### FIPS 199 (FIPS-199)
```json
{
  "framework": "FIPS-199",
  "impact_min": "low" | "moderate" | "high"
}
```
Impact level represents the minimum categorization at which this risk crosses its threshold.

#### OWASP Risk Scoring
Entries with OWASP framework references (e.g., `OWASP-API3:2023`, `OWASP-LLM01:2025`) may optionally declare CVSS or OWASP RiskRating thresholds:
```json
{
  "framework": "OWASP-RiskRating",
  "severity_min": "critical" | "high" | "medium" | "low"
}
```

## CI-Lint Enforcement (SC-001, FR-028)

The linter enforces:
1. Every entry MUST have `id`, `summary`, and `framework_refs` (required fields).
2. Every entry's `framework_refs` list MUST be non-empty (SC-001).
3. Entries citing quantification-required frameworks (NIST-AI-RMF, FAIR, EU-AI-Act, FIPS-199) MUST declare a `threshold` field (FR-028 fail-closed).
4. No duplicate entry ids.
5. Threshold shapes MUST match their declared framework.

## Runtime Enforcement (FR-016)

At decision time:
- When a decision is ALLOW and cites risk-register ids via AxisA labels, the engine checks if any cited ids have thresholds and if the residual risk metrics exceed those thresholds.
- If crossed, a `residual_risk.exception` event is emitted with `crossed_risk_ids` listing the specific ids that exceeded their thresholds.
- Non-ALLOW outcomes (DENY, REQUIRE_APPROVAL) never emit residual-risk exceptions — the gate caught the risk.

## Example: Adding a New Risk Entry

```json
{
  "id": "RISK-MODEL-DRIFT",
  "summary": "Degraded model performance due to input distribution shift.",
  "framework_refs": [
    "NIST-AI-RMF-SM-3",
    "ISO-IEC-42001"
  ],
  "threshold": {
    "framework": "NIST-AI-RMF",
    "impact_tier_min": "tier-2"
  }
}
```

After adding this entry:
1. Run `uv run pytest tests/invariants/test_risk_register_thresholds.py` to lint the schema.
2. Run `uv run pytest tests/test_risk_register_thresholds.py` to verify functional behavior.
3. Update labels/profiles to cite `RISK-MODEL-DRIFT` if the risk is relevant.
4. At decision time, if a decision citing this risk is ALLOW, the engine will check the threshold.
