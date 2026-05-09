import os

BACKEND_PORT = 2121
CGI_PORT = 8080
PING_HOST = "1.1.1.1"
TEST_MINT_URL = os.environ.get("TOLLGATE_TEST_MINT_URL", "https://testnut.cashu.exchange")
TOKEN_MIN = 1
TOKEN_SMALL = 3
TOKEN_DEFAULT = 4
TOKEN_LONG = 10
DEFAULT_STEP_SIZE_MS = 5000
PRODUCTION_STEP_SIZE_MS = 5000

ANDROID_CAPTIVE_PORTAL = "com.android.captiveportallogin"
ANDROID_CAPTIVE_PORTAL_ACTIVITY = "com.android.captiveportallogin/.CaptivePortalLoginActivity"
