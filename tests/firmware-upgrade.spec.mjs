import { test, expect } from '@playwright/test';
import { getRouter } from './helpers/inventory.mjs';
import { ssh, copyToRouter, remotePathFor } from './helpers/ssh.mjs';
import { waitForRouterCommand } from './helpers/router-config.mjs';
import { isSafeForNetworkTests } from './helpers/network.mjs';

test.describe('firmware upgrade', () => {
	test('flashes firmware and verifies router comes back online', async () => {
		const imagePath = process.env.TOLLGATE_FIRMWARE_IMAGE;
		test.skip(!imagePath, 'TOLLGATE_FIRMWARE_IMAGE is required');
		test.skip(!isSafeForNetworkTests(), 'router must be reachable via ethernet');

		const router = getRouter();

		// Capture pre-upgrade info
		const versionBefore = ssh(router, 'cat /etc/openwrt_release | grep RELEASE_VERSION', { check: false });

		// Copy firmware to router
		const remotePath = remotePathFor(imagePath);
		copyToRouter(router, imagePath, remotePath, { timeout: 180000 });

		// Verify file was copied
		const fileSize = ssh(router, `ls -l ${remotePath} | awk '{print $5}'`, { check: false });
		expect(Number.parseInt(fileSize, 10)).toBeGreaterThan(0);

		// Run sysupgrade (non-blocking — router will reboot)
		ssh(router, `sysupgrade -n ${remotePath}`, { check: false, timeout: 10000 });

		// Wait for router to come back (sysupgrade takes ~2 minutes)
		const recovered = waitForRouterCommand(router, 'echo alive', 180000);
		expect(recovered).toBe(true);

		// Verify basic services
		const hostname = ssh(router, 'hostname', { check: false });
		expect(hostname).toBeTruthy();

		// Verify network is functional
		const hasRoute = waitForRouterCommand(router, 'ip route get 1.1.1.1', 60000);
		expect(hasRoute).toBe(true);

		// Check new firmware version
		const versionAfter = ssh(router, 'cat /etc/openwrt_release | grep RELEASE_VERSION', { check: false });
		// Version may or may not change depending on the firmware
		expect(versionAfter).toBeTruthy();
	});
});
