# Decisions

## 2026-05-13
- Open portal in browser via `am start -a VIEW` intent instead of searching notification shade
- Type tokens via ADB `input text` with clipboard broadcast fallback
- Keep URL param test (test_url_param.py) but fix it to use direct browser open
- New test_token_input.py is the "golden path" real-user-flow test (critical tier)
- New test_captive_portal_auto.py tests Android auto-detection separately (extended tier)
- No changes to pay_direct() tests or router-side code
