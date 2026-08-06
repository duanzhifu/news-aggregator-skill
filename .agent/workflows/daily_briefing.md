---
description: Generate and publish an Obsidian daily briefing
---

1. Run the current AI-selection and snapshot publishing pipeline for the requested sources:
   // turbo
   ```powershell
   & "C:\Users\86139\AppData\Local\Programs\Python\Python312\python.exe" scripts\push_to_obsidian.py --source {{source_keys}} --limit {{limit}} --evidence-mode snapshot --vault "{{vault_path}}" --profile {{profile}}
   ```

2. Read the generated notes in `{{vault_path}}/自动获取信息/YYYY-MM-DD/`.

3. Confirm that `今日总结.md` was regenerated from every article note stored for that date.

4. Report fetched, AI-selected, AI-rejected, failed, deduplicated and newly written counts, plus the path to `今日总结.md`.
