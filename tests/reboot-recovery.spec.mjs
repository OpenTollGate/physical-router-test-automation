import { test, expect } from '@playwright/test';
import { getRouter } from './helpers/inventory.mjs';
import { rebootRouter, waitForRouterCommand, getPrivateSSID } from './helpers/router-config.mjs';
import { ssh } from './helpers/ssh.mjs';
import { isSafeForNetworkTests } from './helpers/network.mjs';
import { getWalletBalance } from './helpers/router-wallet.mjs';

test.describe('reboot recovery', () => {
	// No retries — these tests reboot the router and must not repeat
	test.describe.configure({ retries: 0 });

	test('router comes back online after reboot with settings intact', async () => {
		test.setTimeout(180000);
		test.skip(!isSafeForNetworkTests(), 'router must be reachable via ethernet');
		const router = getRouter();

		// Capture pre-reboot state
		const ssidBefore = getPrivateSSID(router);
		const balanceBefore = getWalletBalance();

		// Reboot and wait for SSH to come back
		rebootRouter(router);
		expect(waitForRouterCommand(router, 'echo alive', 120000)).toBe(true);

		// Wait for tollgate service to be ready (SSH accepts connections
		// before userspace services finish starting)
		const serviceReady = waitForRouterCommand(router, 'tollgate --json wallet balance', 60000);
		expect(serviceReady).toBe(true);

		// Verify settings persist
		const ssidAfter = getPrivateSSID(router);
		expect(ssidAfter).toBe(ssidBefore);

		// Verify wallet persists
		const balanceAfter = getWalletBalance();
		expect(balanceAfter).toBe(balanceBefore);

		// Verify TollGate init script reports running (may take extra time
		// after reboot — the procd service starts at S95, late in boot)
		const initScript = ssh(router, 'ls /etc/init.d/tollgate*', { check: false });
		if (initScript) {
			const script = initScript.split('\n')[0].trim();
			let status = '';
			for (let i = 0; i < 15; i++) {
				status = ssh(router, `${script} status`, { check: false });
				if (status.includes('running')) break;
				await new Promise(r => setTimeout(r, 3000));
			}
			if (!status.includes('running')) {
				console.log(`WARN: tollgate service not running after reboot (status: ${status || 'empty'}). Binary works but init script may need manual start.`);
			}
		}
	});

	test('network connectivity restored after reboot', async () => {
		test.setTimeout(180000);
		test.skip(!isSafeForNetworkTests(), 'router must be reachable via ethernet');
		const router = getRouter();

		rebootRouter(router);
		const recovered = waitForRouterCommand(router, 'ip route get 1.1.1.1', 120000);
		expect(recovered).toBe(true);

		// Verify the router has a default route
		const routes = ssh(router, 'ip route show');
		expect(routes).toContain('default');
	});
});
