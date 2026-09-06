---
name: phonetic-intake-verification
description: Capture and verify complex caller emails, reference IDs, and alphanumeric
  codes using NATO phonetics and pronunciation rules.
version: 1.0.0
allowed_tools: []
mcp_tools: []
task_ids: []
languages:
- en
- fr
priority: 78
---
# Phonetic Intake & Alphanumeric Verification

- When transcribing or verifying an email address, pronounce:
  - "@" as "at"
  - "." as "dot"
  - "-" as "dash"
  - "@gmail.com" as "at g-mail dot com"
  - "@outlook.com" as "at outlook dot com"
- When letters are unclear over phone audio, disambiguate using standard phonetics:
  - A for Apple, B for Bravo, C for Charlie, D for Delta, E for Echo, F for Foxtrot
  - G for Golf, H for Hotel, I for India, J for Juliet, K for Kilo, L for Lima
  - M for Mike, N for November, O for Oscar, P for Papa, Q for Quebec, R for Romeo
  - S for Sierra, T for Tango, U for Uniform, V for Victor, W for Whiskey, X for X-ray
  - Y for Yankee, Z for Zebra
- Number Chunking: Repeat numbers in groups of 3 or 4 digits with natural pauses.
- Read-Back Verification: Always echo the parsed value back to the caller in one short question to confirm accuracy before proceeding.
