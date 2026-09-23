# Roadmap / ideas

Comment on (or open) an issue first so we don't duplicate work.

## Good first issues
- [ ] Add screenshots (from the sample data) to the README
- [ ] Split the 1,400-line `app.py` into Flask blueprints
- [ ] Add unit tests for `rules.py` (categorisation) and `reconcile.py` (matching)
- [ ] Support more bank CSV formats (a small "profile" per bank)
- [ ] Add a light/dark theme toggle

## Security and privacy
- [ ] Optional login / PIN, required for LAN mode
- [ ] Encrypt SMTP/SMS secrets and JSON backups
- [ ] Make favicon fetching optional (or bundle a few local icons) for fully offline use

## Features
- [ ] Multiple accounts (savings, credit cards) and transfers
- [ ] OFX/QFX import
- [ ] Savings goals and sinking funds
- [ ] Shared household mode (two people, one dataset)
- [ ] GitHub Actions: lint, tests and a packaged Windows `.exe` on each release
