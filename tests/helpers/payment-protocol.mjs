import { execSync } from 'child_process';
import { existsSync, mkdirSync, writeFileSync, readFileSync, unlinkSync } from 'fs';
import { join } from 'path';
import { tmpdir } from 'os';
import { runCommand } from './command.mjs';
import { hostMacAddress } from './network.mjs';

const LOCK_DIR = join(tmpdir(), 'cashu-lock');

function withLock(fn) {
	if (!existsSync(LOCK_DIR)) mkdirSync(LOCK_DIR, { recursive: true });
	const lockFile = join(LOCK_DIR, 'mint.lock');
	let acquired = false;
	for (let i = 0; i < 60; i++) {
		try {
			writeFileSync(lockFile, process.pid.toString(), { flag: 'wx' });
			acquired = true;
			break;
		} catch {
			const age = Date.now() - (parseInt(readFileSync(lockFile, 'utf8').split('\n')[1] || '0', 10) || 0);
			if (age > 30000) { try { unlinkSync(lockFile); } catch {} }
			execSync('sleep 1', { timeout: 2000 });
		}
	}
	if (!acquired) {
		try { unlinkSync(lockFile); } catch {}
		writeFileSync(lockFile, process.pid.toString(), { flag: 'wx' });
	}
	writeFileSync(lockFile, process.pid.toString() + '\n' + Date.now());
	try {
		return fn();
	} finally {
		try { unlinkSync(lockFile); } catch {}
	}
}

const MINT_URL = process.env.TOLLGATE_TEST_MINT_URL || 'https://testnut.cashu.exchange';
const CASHU = `echo "" | cashu -h ${MINT_URL}`;

export function mintTestnutTokens(amountSats) {
	return withLock(() => {
		const mintAmount = amountSats + 10;
		const createOut = execSync(`${CASHU} invoice ${mintAmount} --no-check`, { encoding: 'utf8', timeout: 30000, shell: '/bin/bash' });
		const idMatch = createOut.match(/--id ([a-f0-9]+)/);
		if (idMatch) {
			execSync(`${CASHU} invoice ${mintAmount} --id ${idMatch[1]}`, { encoding: 'utf8', timeout: 30000, shell: '/bin/bash' });
		}
		const out = execSync(`${CASHU} send ${amountSats}`, { encoding: 'utf8', timeout: 30000, shell: '/bin/bash' });
		const lines = out.split('\n');
		const tokenLine = lines.find(l => l.startsWith('cashuA') || l.startsWith('cashuB'));
		if (!tokenLine) throw new Error('No token in cashu output: ' + out);
		return tokenLine.trim();
	});
}

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
