---
name: skill.extract_dose
description: >-
  Extract a structured DoseSummary (medication name, dosage in mg,
  frequency) from prescription text. Schema-validated; output is the
  declassification gate.
capability_kind: READ_FS
inherent_labels: []
schema: DoseSummary
target_arg: text
parameters:
  type: object
  properties:
    text:
      type: string
      description: The raw prescription text to extract from.
  required:
    - text
---
Extract a DoseSummary from the following text. Match every field of the
schema; do not include anything else.

{{text}}
