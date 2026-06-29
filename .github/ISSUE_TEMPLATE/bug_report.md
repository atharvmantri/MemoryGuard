name: Bug report
description: Report a MemoryGuard public alpha bug
body:
  - type: textarea
    id: summary
    attributes:
      label: Summary
      description: What happened?
    validations:
      required: true
  - type: textarea
    id: repro
    attributes:
      label: Reproduction
      description: Steps to reproduce. Redact secrets.
