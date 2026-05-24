
# Learnings

## Title
Test URL Parameter Rewrite

## Key Changes
- Changed from `adb.start_activity()` with `data_uri` to `adb.open_url()` for cleaner code
- Fixed port from 8080 to 2050 (NDS portal listens on 2050, not 8080)
- Used `wifi._get_portal_host()` instead of hardcoded "192.168.1.1"
- Removed all captive portal notification and WiFi Settings references
- Added `logging` import for debugging capability

## Success Criteria Met
- ✅ File modified: `tests/phone/test_url_param.py`
- ✅ Test uses `adb.open_url()` to open portal directly
- ✅ Test uses `connected_wifi` fixture for WiFi connection
- ✅ No reference to captive portal notification, notification shade, "Sign in", or WiFi Settings
- ✅ Timeout remains 120s
- ✅ Markers: `pytest.mark.phone, pytest.mark.slow, pytest.mark.timeout(120), pytest.mark.extended`
- ✅ `python -m py_compile tests/phone/test_url_param.py` passes

## Test Flow
1. Use `connected_wifi` fixture to connect WiFi (calls `wifi.reconnect()` which opens portal via browser)
2. Mint a cashu token: `token = cashu.mint(TOKEN_DEFAULT)`
3. Get the portal host: `portal_host = router.domain or wifi._get_portal_host()`
4. Encode the token: `encoded = urllib.parse.quote(token)`
5. Open portal URL with token param: `adb.open_url(f"http://{portal_host}:2050/?token={encoded}")`
6. Wait for auth: `router.wait_for_auth(timeout=60)`
7. Assert session active: `assert_session_active(router)`
8. Take screenshot: `screenshot_portal("url-param-final.png")`
