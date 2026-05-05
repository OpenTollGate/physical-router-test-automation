import { runCommand } from './command.mjs';
import { hostMacAddress } from './network.mjs';

export async function fetchDiscoveryEvent(routerIp) {
	const response = await fetch(`http://${routerIp}:2121/`);
	if (!response.ok) throw new Error(`Discovery fetch failed: ${response.status}`);
	const event = await response.json();
	if (event.kind !== 10021) throw new Error(`Unexpected discovery kind: ${event.kind}`);
	return event;
}

export function pricePerStep(discoveryEvent, mintUrl = process.env.TOLLGATE_TEST_MINT_URL || 'https://testnut.cashu.exchange') {
	const tag = discoveryEvent.tags?.find(value => value[0] === 'price_per_step' && value[1] === 'cashu' && value[4] === mintUrl);
	if (!tag) throw new Error(`No cashu price_per_step tag for ${mintUrl}`);
	return Number.parseInt(tag[2], 10);
}

export function generateCustomerIdentity() {
	const secretKey = runCommand('nak', ['key', 'generate'], { timeout: 15000 }).stdout.trim();
	const publicKey = runCommand('nak', ['key', 'public', secretKey], { timeout: 15000 }).stdout.trim();
	return { secretKey, publicKey };
}

export function signPaymentEvent({ secretKey, publicKey, tollgatePublicKey, macAddress, cashuToken }) {
	const unsigned = JSON.stringify({
		kind: 21000,
		pubkey: publicKey,
		tags: [['p', tollgatePublicKey], ['device-identifier', 'mac', macAddress], ['payment', cashuToken]],
		content: '',
	});
	return JSON.parse(runCommand('nak', ['event', '--sec', secretKey], { input: unsigned, timeout: 15000 }).stdout);
}

export async function sendPaymentEvent(routerIp, paymentEvent) {
	const response = await fetch(`http://${routerIp}:2121/`, {
		method: 'POST',
		headers: { 'content-type': 'application/json' },
		body: JSON.stringify(paymentEvent),
	});
	const text = await response.text();
	if (!response.ok) throw new Error(`Payment POST failed: ${response.status} ${text}`);
	const event = JSON.parse(text);
	if (event.kind !== 1022) throw new Error(`Unexpected payment response kind: ${event.kind}`);
	return event;
}

export function paymentMacAddress(interfaceName = process.env.TOLLGATE_WIFI_INTERFACE) {
	if (!interfaceName) throw new Error('TOLLGATE_WIFI_INTERFACE is required for payment protocol tests');
	return hostMacAddress(interfaceName);
}

export function canReachInternet(host = process.env.TOLLGATE_CONNECTIVITY_HOST || '8.8.8.8') {
	return runCommand('ping', ['-c', '1', '-W', '5', host], { check: false, timeout: 10000 }).status === 0;
}
