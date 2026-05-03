# Auth helper scripts

`web_auth.py` saves and validates Playwright `storage_state` files for web-console services.

Examples:

```bash
poetry run python scripts/auth/web_auth.py save --service wechat
poetry run python scripts/auth/web_auth.py test --service wechat --auth wechat_auth/state.json

poetry run python scripts/auth/web_auth.py save --service chatgpt
poetry run python scripts/auth/web_auth.py test --service chatgpt --auth auth_profiles/saved/chatgpt_state.json --chatgpt-no-send
```

See `docs/AUTH_STATE_TOOLS.md` for full usage and security notes.
