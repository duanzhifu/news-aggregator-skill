---
description: Generate and publish an Obsidian daily briefing
---

1. Run the daily pipeline via the cross-platform entrypoint (auto-resolves Vault, browser profile and sources from skill-root config; refuses to write to the main vault):
   ```powershell
   python scripts/run_daily.py
   ```
   For a one-off custom run (custom sources / limits / vault), call the pipeline directly instead:
   ```powershell
   python scripts/push_to_obsidian.py --source {{source_keys}} --limit {{limit}} --evidence-mode snapshot --vault "{{vault_path}}"
   ```

2. Read the generated notes in `<Vault>/自动获取信息/YYYY-MM-DD/`.

3. Confirm that `今日总结.md` was regenerated from every article note stored for that date.

4. Report fetched, AI-selected, AI-rejected, failed, deduplicated and newly written counts, plus the path to `今日总结.md`.
