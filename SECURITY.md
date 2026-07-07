# Security

## Secrets

Never commit `.env`. It may contain:

- `NOTION_TOKEN`
- `NOTION_DATABASE_ID`

Copy from `.env.example` and keep real values local only.

If a secret is ever committed or pushed, rotate it immediately in Notion (revoke the integration token and create a new one).

## Confirm no secrets before making the repo public

Run these from the project root:

```bash
# 1. .env should never appear in git history
git log --all --oneline -- .env
# Expected: no output

# 2. List every file ever committed
git log --name-only --pretty=format: | sort -u | grep -v '^$'

# 3. Search all commits for common secret patterns (placeholders are OK)
git log -p --all | grep -iE \
  'NOTION_TOKEN=(secret_|ntn_|$)|NOTION_DATABASE_ID=[a-f0-9]{20,}|ghp_|gho_|discord.*https?://|api[_-]?key\s*=\s*[^y]' \
  || echo "No suspicious patterns in history"

# 4. Confirm sensitive local files are ignored
git check-ignore -v .env price_history.db venv/ tracker.log

# 5. Nothing sensitive staged right now
git status
```

Optional: install [gitleaks](https://github.com/gitleaks/gitleaks) for a deeper scan:

```bash
brew install gitleaks
gitleaks detect --source . --verbose
```

## Reporting

This is a personal project. If you find a security issue in the published code, open a GitHub issue on the repository.
