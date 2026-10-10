# Campaign full1 (100 single-fault cases)

`report.md` is the readable summary; `by_config.csv` / `cases.csv` / `summary.csv` are flat tables; `summary.json` has
every case summary. The per-request data (`campaign_all.json`, `all_requests.csv`, each case's `requests.jsonl`) is
too large for git: regenerate it with `python -m chaos.campaign --repeats 7 --limit 100 --rate 20`
or ask P3 for `full1.zip`. Field notes and caveats: see the main README ("Fault campaign").
