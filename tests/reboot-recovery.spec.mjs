import { test, expect } from '@playwright/test';
import { getRouter } from './helpers/inventory.mjs';
import { rebootRouter, waitForRouterCommand, getPrivateSSID } from './helpers/router-config.mjs';
import { ssh } from './helpers/ssh.mjs';
import { isSafeForNetworkTests } from './helpers/network.mjs';
import { getWalletBalance } from './helpers/router-wallet.mjs';

test.describe('reboot recovery', () => {
	test('router comes back online after reboot with settings intact', async () => {
		test.skip(!isSafeForNetworkTests(), 'router must be reachable via ethernet');
		const router = getRouter();

		// Capture pre-reboot state
		const ssidBefore = getPrivateSSID(router);
		const balanceBefore = getWalletBalance();

		// Reboot and wait
		rebootRouter(router);
		const recovered = waitForRouterCommand(router, 'echo alive', 120000);
		expect(recovered).toBe(true);

		// Verify settings persist
		const ssidAfter = getPrivateSSID(router);
		expect(ssidAfter).toBe(ssidBefore);

		// Verify wallet persists
		const balanceAfter = getWalletBalance();
		expect(balanceAfter).toBe(balanceBefore);

		// Verify TollGate service is running
		const services = ssh(router, 'ls /etc/init.d/tollgate*', { check: false });
		if (services) {
			const status = ssh(router, '/etc/init.d/tollgate status', { check: false });
			// Service may take a moment to start after reboot, give it time
			if (!status.includes('running')) {
				await new Promise(r => setTimeout(r, 5000));
			}
			const statusRetry = ssh(router, '/etc/init.d/tollgate status', { check: false });
			expect(statusRetry).toContain('running');
		}
	});

	test('network connectivity restored after reboot', async () => {
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
