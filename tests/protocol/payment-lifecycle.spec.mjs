import { test, expect } from '@playwright/test';
import { getRouter } from '../helpers/inventory.mjs';
import { findTollGateNetworks, connectToWifi, restoreWifi, currentWifiConnection, gatewayForInterface } from '../helpers/network.mjs';
import { mintTestnutTokens, fetchDiscoveryEvent, pricePerStep, generateCustomerIdentity, signPaymentEvent, sendPaymentEvent, paymentMacAddress, canReachInternet } from '../helpers/payment-protocol.mjs';

test.describe('payment lifecycle', () => {
	test('pay → use → disconnect → pay again', async () => {
		test.skip(process.env.TOLLGATE_ENABLE_WIFI_CLIENT_TESTS !== 'true', 'set TOLLGATE_ENABLE_WIFI_CLIENT_TESTS=true');
		const router = getRouter();
		const previous = currentWifiConnection();

		try {
			// Cycle 1: Pay and verify
			const networks = findTollGateNetworks(router);
			expect(networks.length).toBeGreaterThan(0);
			connectToWifi(networks[0], router);
			const routerIp = gatewayForInterface(router.wifiInterface);

			const discovery = await fetchDiscoveryEvent(routerIp);
			const steps = Number.parseInt(process.env.TOLLGATE_PAYMENT_STEPS || '100', 10);
			const amount = pricePerStep(discovery) * steps;
			const token = mintTestnutTokens(amount);
			const identity = generateCustomerIdentity();
			const macAddress = paymentMacAddress(router.wifiInterface);

			const paymentEvent = signPaymentEvent({
				secretKey: identity.secretKey,
				publicKey: identity.publicKey,
				tollgatePublicKey: discovery.pubkey,
				macAddress,
				cashuToken: token,
			});
			await sendPaymentEvent(routerIp, paymentEvent);
			expect(canReachInternet()).toBe(true);

			// Disconnect
			connectToWifi(networks[0], router); // reconnect to same network

			// Cycle 2: Pay again and verify
			const routerIp2 = gatewayForInterface(router.wifiInterface);
			const discovery2 = await fetchDiscoveryEvent(routerIp2);
			const token2 = mintTestnutTokens(amount);
			const identity2 = generateCustomerIdentity();
			const paymentEvent2 = signPaymentEvent({
				secretKey: identity2.secretKey,
				publicKey: identity2.publicKey,
				tollgatePublicKey: discovery2.pubkey,
				macAddress,
				cashuToken: token2,
			});
			await sendPaymentEvent(routerIp2, paymentEvent2);
			expect(canReachInternet()).toBe(true);
		} finally {
			restoreWifi(previous);
		}
	});
});
