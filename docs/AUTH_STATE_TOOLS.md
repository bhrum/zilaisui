# Web Auth State Tools

This repository includes `scripts/auth/web_auth.py`, a Playwright helper for saving and validating browser `storage_state` files.

Supported services:

- `wechat`: WeChat Official Account backend (`mp.weixin.qq.com`)
- `chatgpt`: ChatGPT web (`chatgpt.com`)

The helper does **not** bypass Cloudflare, captcha, QR-code login, account limits, or any other access control. It opens a normal browser, waits for the user to complete the normal login flow, then saves cookies and localStorage.

## Install browser dependencies

```bash
poetry install
poetry run playwright install chromium
```

Without Poetry:

```bash
pip install playwright python-dotenv
python -m playwright install chromium
```

## Save WeChat Official Account auth

```bash
WECHAT_ENABLED=true WECHAT_HEADLESS=false \
poetry run python scripts/auth/web_auth.py save \
  --service wechat \
  --auth wechat_auth/state.json \
  --profile wechat_auth/profile \
  --timeout 300
```

Scan the QR code in the opened browser. A successful run writes `wechat_auth/state.json`.

Validate it:

```bash
poetry run python scripts/auth/web_auth.py test \
  --service wechat \
  --auth wechat_auth/state.json
```

Expected success shape:

```json
{
  "ok": true,
  "service": "wechat",
  "url": "https://mp.weixin.qq.com/cgi-bin/home?...token=...",
  "cookies": 20,
  "account_name": "your account name"
}
```

## Save ChatGPT web auth

```bash
poetry run python scripts/auth/web_auth.py save \
  --service chatgpt \
  --auth auth_profiles/saved/chatgpt_state.json \
  --profile camoufox_profile/chatgpt_profile \
  --timeout 300
```

Complete the normal ChatGPT login in the opened browser. The generated auth JSON is a sensitive reusable browser session.

Validate login without sending a message:

```bash
poetry run python scripts/auth/web_auth.py test \
  --service chatgpt \
  --auth auth_profiles/saved/chatgpt_state.json \
  --chatgpt-no-send \
  --headless
```

Validate that the UI can send a prompt and receive a response:

```bash
poetry run python scripts/auth/web_auth.py test \
  --service chatgpt \
  --auth auth_profiles/saved/chatgpt_state.json \
  --prompt "只回复 pong" \
  --headless
```

## Proxy and browser overrides

The script automatically uses `UNIFIED_PROXY_CONFIG`, `HTTPS_PROXY`, or `HTTP_PROXY` when present. You can also pass a proxy directly:

```bash
poetry run python scripts/auth/web_auth.py save \
  --service wechat \
  --proxy http://127.0.0.1:7890
```

If Playwright cannot download its bundled browser, point to an existing binary:

```bash
python scripts/auth/web_auth.py test \
  --service wechat \
  --auth wechat_auth/state.json \
  --executable-path /usr/bin/chromium
```

## Diagnostics

The tool writes status JSON and screenshots under `auth_diagnostics/` by default:

- `<service>_auth.json`
- `<service>_auth.png`
- `<service>_smoke.json`
- `<service>_smoke.png`

Do not upload screenshots or auth JSON publicly if they expose account details.
