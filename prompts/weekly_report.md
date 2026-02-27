Generate the weekly insight report and broadcast to all language channels.

## Steps

1. Generate and send the weekly report with multilingual broadcast:
```bash
python3 weekly_insight_report.py --broadcast-languages en,ja
```

## Important Notes
- Run on Sundays only
- Report includes: weekly trade summary, sell evaluations, AI learning insights
- Multilingual broadcast: Korean (default) + English, Japanese
- Uses trading_intuitions from compression for long-term learning insights
