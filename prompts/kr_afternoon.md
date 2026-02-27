Run the Korean stock market afternoon analysis pipeline.

## Steps

1. Run trigger batch for afternoon detection:
```bash
python3 trigger_batch.py afternoon INFO
```

2. Run the afternoon analysis orchestrator (includes tracking, sell decisions, portfolio updates):
```bash
python3 stock_analysis_orchestrator.py --mode afternoon
```

3. Update the dashboard after analysis:
```bash
python3 examples/generate_dashboard_json.py
```

## Important Notes
- Execute steps sequentially
- Afternoon mode includes stock tracking (buy/sell decisions) for existing holdings
- If trigger batch finds no stocks, step 2 still runs for portfolio tracking
- All reports must use formal Korean (합쇼체)
- Check logs for errors after each step
