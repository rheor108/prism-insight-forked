Run the Korean stock market morning analysis pipeline.

## Steps

1. Run trigger batch to detect surging stocks:
```bash
python3 trigger_batch.py morning INFO
```

2. Run the full morning analysis orchestrator (includes report generation, PDF conversion, Telegram delivery):
```bash
python3 stock_analysis_orchestrator.py --mode morning
```

3. After orchestrator completes, update the dashboard:
```bash
python3 examples/generate_dashboard_json.py
```

## Important Notes
- Execute steps sequentially (each depends on the previous)
- If trigger batch finds no stocks, skip step 2
- All reports must use formal Korean (합쇼체)
- Check logs for errors after each step
- Current date/time: use system time (Asia/Seoul timezone)
