import { runCommand } from './command.mjs';
import { getRouter } from './inventory.mjs';

export function currentWifiConnection() {
	const result = runCommand('nmcli', ['connection', 'show', '--active'], { check: false, timeout: 10000 });
	const line = result.stdout.split('\n').find(value => value.includes('wifi'));
	return line?.trim().split(/\s{2,}/)[0] || '';
}

export function findTollGateNetworks(router = getRouter()) {
	const result = runCommand('nmcli', ['-f', 'SSID', 'device', 'wifi', 'list'], { timeout: 20000 });
	return [...new Set(result.stdout.split('\n').slice(1).map(line => line.trim()).filter(ssid => ssid.startsWith(router.tollgateSsidPrefix)))].sort();
}

export function connectToWifi(ssid, router = getRouter(), attempts = 10) {
	if (!router.wifiInterface) throw new Error('TOLLGATE_WIFI_INTERFACE is required for WiFi tests');
	for (let attempt = 1; attempt <= attempts; attempt++) {
		runCommand('nmcli', ['device', 'disconnect', router.wifiInterface], { check: false, timeout: 10000 });
		const result = runCommand('nmcli', ['device', 'wifi', 'connect', ssid, 'ifname', router.wifiInterface], { check: false, timeout: 30000 });
		if (result.status === 0) return ssid;
		runCommand('sleep', ['2'], { timeout: 3000 });
	}
	throw new Error(`Failed to connect to ${ssid}`);
}

export function restoreWifi(connectionName) {
	if (!connectionName) return;
	runCommand('nmcli', ['connection', 'up', connectionName], { check: false, timeout: 30000 });
}

export function gatewayForInterface(interfaceName) {
	const result = runCommand('ip', ['route', 'show', 'dev', interfaceName], { timeout: 10000 });
	const line = result.stdout.split('\n').find(value => value.startsWith('default via'));
	const match = line?.match(/default via ([^ ]+)/);
	if (!match) throw new Error(`No default gateway for ${interfaceName}`);
	return match[1];
}

export function hostMacAddress(interfaceName) {
	const result = runCommand('ip', ['link', 'show', interfaceName], { timeout: 10000 });
	const match = result.stdout.match(/link\/ether\s+([^\s]+)/);
	if (!match) throw new Error(`No MAC address for ${interfaceName}`);
	return match[1];
}

export function isSafeForNetworkTests() {
	const routerIP = getRouter().sshHost;
	try {
		const routeOut = runCommand('netstat', ['-rn'], { check: false, timeout: 5000 });
		const routeLine = routeOut.stdout.split('\n').find(l => l.includes(routerIP) && l.includes('UH'));
		if (!routeLine) return false;
		const match = routeLine.match(/\s+(en\d+|eth\d+)\s+/);
		if (!match) return false;
		try { runCommand('ping', ['-c', '1', '-t', '2', routerIP], { check: false, timeout: 5000 }); } catch { return false; }
		return true;
	} catch {
		return false;
	}
}
