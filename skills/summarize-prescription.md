---
name: skill.summarize_prescription
description: >-
  Summarize a prescription label into a single concise sentence.
  Inherits confidential.health so the output stays inside the
  health-tagged compartment.
capability_kind: READ_FS
inherent_labels:
  - confidential.health
target_arg: text
parameters:
  type: object
  properties:
    text:
      type: string
      description: The raw prescription text to summarize.
  required:
    - text
---
You are summarizing a prescription label for the patient who wrote it.
Produce a single sentence that names the medication, the dosage, and
the frequency. Do not add any disclaimers or commentary.

Prescription text:

{{text}}
