// Shared cursor-highlight overlay for Playwright demo video recordings.
//
// Playwright has no built-in cursor visualisation in headless recordings, so we
// inject a glowing dot that tracks the mouse plus an expanding ripple on every
// click. This makes demo videos easy to follow. The overlay is pure DOM/CSS —
// no network resources, no dependencies — so it is safe to inject anywhere and
// trivially unit-testable.
//
// Exports:
//   CURSOR_HIGHLIGHT_CSS  — the overlay stylesheet (single source of truth)
//   CURSOR_HIGHLIGHT_JS   — self-contained injection script (embeds the CSS)
//   injectCursorHighlight(page) — convenience: inject the overlay into a page
//   smoothMove(page, selector, steps) — eased mouse move to an element
//   smoothMoveXY(page, x, y, steps)  — eased mouse move to absolute coords

// ---------------------------------------------------------------------------
// Stylesheet
// ---------------------------------------------------------------------------

export const CURSOR_HIGHLIGHT_CSS = `
#pw-cursor {
  position: fixed;
  width: 24px;
  height: 24px;
  pointer-events: none;
  z-index: 999999;
  transition: transform 0.08s ease-out, opacity 0.2s;
  transform: translate(-50%, -50%);
}
#pw-cursor::before {
  content: '';
  position: absolute;
  top: 0; left: 0;
  width: 24px; height: 24px;
  background: rgba(255, 165, 0, 0.9);
  border: 2px solid rgba(255, 255, 255, 0.9);
  border-radius: 50%;
  box-shadow: 0 0 12px rgba(255, 165, 0, 0.6), 0 0 24px rgba(255, 165, 0, 0.3);
}
#pw-cursor.clicking::before {
  animation: pw-click-pulse 0.4s ease-out;
}
@keyframes pw-click-pulse {
  0% { transform: scale(1); box-shadow: 0 0 12px rgba(255, 165, 0, 0.6); }
  50% { transform: scale(1.8); box-shadow: 0 0 30px rgba(255, 69, 0, 0.8); border-width: 3px; }
  100% { transform: scale(1); box-shadow: 0 0 12px rgba(255, 165, 0, 0.6); }
}
#pw-click-ripple {
  position: fixed;
  pointer-events: none;
  z-index: 999998;
  width: 60px; height: 60px;
  border: 3px solid rgba(255, 165, 0, 0.6);
  border-radius: 50%;
  transform: translate(-50%, -50%);
  animation: pw-ripple-expand 0.6s ease-out forwards;
}
@keyframes pw-ripple-expand {
  0% { width: 20px; height: 20px; opacity: 1; border-width: 4px; }
  100% { width: 80px; height: 80px; opacity: 0; border-width: 1px; }
}
`;

// ---------------------------------------------------------------------------
// Self-contained injection script
//
// Embeds CURSOR_HIGHLIGHT_CSS so a single `page.evaluate(CURSOR_HIGHLIGHT_JS)`
// is enough to install the whole overlay (style + cursor node + listeners).
// Deliberately references no URLs / external resources.
// ---------------------------------------------------------------------------

export const CURSOR_HIGHLIGHT_JS = `
(function () {
  var css = ${JSON.stringify(CURSOR_HIGHLIGHT_CSS)};
  var style = document.createElement('style');
  style.textContent = css;
  (document.head || document.documentElement).appendChild(style);

  var cursor = document.createElement('div');
  cursor.id = 'pw-cursor';
  (document.body || document.documentElement).appendChild(cursor);

  document.addEventListener('mousemove', function (e) {
    cursor.style.left = e.clientX + 'px';
    cursor.style.top = e.clientY + 'px';
  }, true);

  document.addEventListener('click', function (e) {
    cursor.classList.add('clicking');
    setTimeout(function () { cursor.classList.remove('clicking'); }, 400);

    var ripple = document.createElement('div');
    ripple.id = 'pw-click-ripple';
    ripple.style.left = e.clientX + 'px';
    ripple.style.top = e.clientY + 'px';
    document.body.appendChild(ripple);
    setTimeout(function () { ripple.remove(); }, 600);
  }, true);

  // Park the cursor in the centre until the first mouse move.
  cursor.style.left = '50%';
  cursor.style.top = '50%';
})();
`;

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Inject the cursor-highlight overlay into a Playwright page.
 * Safe to call more than once; repeated calls just re-add the nodes.
 *
 * @param {import('@playwright/test').Page} page
 * @returns {Promise<import('@playwright/test').Page>} the same page (for chaining)
 */
export async function injectCursorHighlight(page) {
	// addStyleTag is a no-op if the JS below already added it; keeps callers that
	// only use the CSS string happy too.
	await page.addStyleTag({ content: CURSOR_HIGHLIGHT_CSS });
	await page.evaluate(CURSOR_HIGHLIGHT_JS);
	return page;
}

/**
 * Ease the mouse towards the centre of an element in `steps` micro-moves so the
 * recording shows a natural glide instead of a teleport.
 *
 * @param {import('@playwright/test').Page} page
 * @param {string} selector — Playwright locator selector
 * @param {number} [steps=15]
 */
export async function smoothMove(page, selector, steps = 15) {
	try {
		const target = page.locator(selector).first();
		const box = await target.boundingBox();
		if (!box) return;
		await smoothMoveXY(page, box.x + box.width / 2, box.y + box.height / 2, steps);
	} catch {
		// Selector may not exist on this portal variant — skip silently.
	}
}

/**
 * Ease the mouse to absolute viewport coordinates (x, y) over `steps` moves
 * using a quadratic ease-in-out. The origin point is offset from (200, 200) so
 * the glide is visible even when the target is near the top-left.
 *
 * @param {import('@playwright/test').Page} page
 * @param {number} x
 * @param {number} y
 * @param {number} [steps=15]
 */
export async function smoothMoveXY(page, x, y, steps = 15) {
	const originX = 200;
	const originY = 200;
	for (let i = 1; i <= steps; i++) {
		const t = i / steps;
		const eased = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
		await page.mouse.move(x * eased + originX * (1 - eased), y * eased + originY * (1 - eased));
		await page.waitForTimeout(20);
	}
	await page.mouse.move(x, y);
}
