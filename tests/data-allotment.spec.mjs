import { test, expect } from '@playwright/test';
import { runCommand } from './helpers/command.mjs';
import { canReachInternet } from './helpers/payment-protocol.mjs';

test.describe('data allotment enforcement', () => {
	test('cuts connectivity after traffic consumes the paid allotment', async () => {
		test.skip(process.env.TOLLGATE_ENABLE_DATA_ALLOTMENT_TESTS !== 'true', 'set TOLLGATE_ENABLE_DATA_ALLOTMENT_TESTS=true after connecting and paying through TollGate');
		const downloadUrl = process.env.TOLLGATE_DATA_TEST_URL || 'https://nbg1-speed.hetzner.com/100MB.bin';
		const timeoutSeconds = Number.parseInt(process.env.TOLLGATE_DATA_TEST_TIMEOUT || '300', 10);
		expect(canReachInternet()).toBe(true);
		const result = runCommand('curl', ['--fail', '--location', '--output', '/dev/null', '--max-time', String(timeoutSeconds), downloadUrl], { check: false, timeout: (timeoutSeconds + 10) * 1000 });
		expect(result.status).not.toBe(0);
		expect(canReachInternet()).toBe(false);
	});
});
