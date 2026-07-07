# Security

## Secrets

Never commit `.env`. It may contain:

- `NOTION_TOKEN`
- `NOTION_DATABASE_ID`

Copy from `.env.example` and keep real values local only.

If a secret is ever committed or pushed, rotate it immediately in Notion (revoke the integration token and create a new one).

## Audit commands

Run these from the project root before pushing, or anytime you want peace of mind:

```bash
# .env should never appear in git history
git log --all --oneline -- .env

# Sensitive local files must be gitignored
git check-ignore -v .env price_history.db venv/ tracker.log

# Nothing sensitive staged
git status

# Notion lines in history should be placeholders only
git log -p --all | grep 'NOTION_TOKEN='
```

Optional deeper scan:

```bash
gitleaks detect --source . --verbose
```

## Reporting

This is a personal project. If you find a security issue in the published code, open a GitHub issue on the repository.
