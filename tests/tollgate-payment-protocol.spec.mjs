import { test, expect } from '@playwright/test';
import { getRouter } from './helpers/inventory.mjs';
import { findTollGateNetworks, connectToWifi, restoreWifi, currentWifiConnection, gatewayForInterface } from './helpers/network.mjs';
import { mintTestnutTokens } from './helpers/payment-protocol.mjs';
import { fetchDiscoveryEvent, pricePerStep, generateCustomerIdentity, signPaymentEvent, sendPaymentEvent, paymentMacAddress, canReachInternet } from './helpers/payment-protocol.mjs';

test.describe('payment protocol', () => {
	test('pays a TollGate network and verifies connectivity', async () => {
		test.skip(process.env.TOLLGATE_ENABLE_WIFI_CLIENT_TESTS !== 'true', 'set TOLLGATE_ENABLE_WIFI_CLIENT_TESTS=true to change host WiFi');
		const router = getRouter();
		const previous = currentWifiConnection();
		try {
			const networks = findTollGateNetworks(router);
			expect(networks.length).toBeGreaterThan(0);
			connectToWifi(networks[0], router);
			const routerIp = gatewayForInterface(router.wifiInterface);
			const discovery = await fetchDiscoveryEvent(routerIp);
			const amount = pricePerStep(discovery) * Number.parseInt(process.env.TOLLGATE_PAYMENT_STEPS || '100', 10);
			const token = mintTestnutTokens(amount);
			const identity = generateCustomerIdentity();
			const event = signPaymentEvent({
				secretKey: identity.secretKey,
				publicKey: identity.publicKey,
				tollgatePublicKey: discovery.pubkey,
				macAddress: paymentMacAddress(router.wifiInterface),
				cashuToken: token,
			});
			await sendPaymentEvent(routerIp, event);
			expect(canReachInternet()).toBe(true);
		} finally {
			restoreWifi(previous);
		}
	});
});
