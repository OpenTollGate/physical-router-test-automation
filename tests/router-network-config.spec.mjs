import { test, expect } from '@playwright/test';
import { getRouter } from './helpers/inventory.mjs';
import { ensureWwanInterface, configureStationInterfaces, restartNetwork, waitForRouterCommand } from './helpers/router-config.mjs';
import { isSafeForNetworkTests } from './helpers/router.mjs';

test.describe('router network configuration', () => {
	test('configures upstream station interface and keeps router online', async () => {
		test.skip(!process.env.TOLLGATE_UPSTREAM_SSID || !process.env.TOLLGATE_UPSTREAM_WIFI_PASSWORD, 'upstream WiFi env is required');
		test.skip(!isSafeForNetworkTests(), 'router must be reachable without using the TollGate client WiFi');
		const router = getRouter();
		ensureWwanInterface(router);
		configureStationInterfaces({ router });
		expect(restartNetwork(router)).toBe(true);
		expect(waitForRouterCommand(router, 'ip route get 1.1.1.1', 30000)).toBe(true);
	});
});
