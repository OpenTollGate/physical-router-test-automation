# Issues

## None
No issues encountered during the rewrite. The test flow is now clean and doesn't rely on broken captive portal notification flow.

## Potential Future Improvements
- Could add explicit check for portal state before opening URL (optional)
- Could add timeout for portal rendering (currently handled by `router.wait_for_auth()`)
- Could add logging of portal URL for debugging
