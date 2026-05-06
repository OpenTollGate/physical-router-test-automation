import { readFileSync, existsSync } from 'fs';
import { resolve } from 'path';

const inventoryPath = resolve(process.env.TOLLGATE_ROUTER_INVENTORY || 'config/routers.json');

function loadInventory() {
	if (!existsSync(inventoryPath)) return null;
	return JSON.parse(readFileSync(inventoryPath, 'utf8'));
}

export function getRouter(routerId = process.env.TOLLGATE_ROUTER_ID) {
	const inventory = loadInventory();
	const selected = routerId || inventory?.default;
	const router = selected ? inventory?.routers?.[selected] : null;
	const luciUrl = process.env.TOLLGATE_LUCI_URL || router?.luciUrl || 'http://192.168.13.112:8080';
	const sshHost = process.env.TOLLGATE_SSH_HOST || router?.sshHost || new URL(luciUrl).hostname;
	return {
		id: selected || process.env.TOLLGATE_ROUTER_ID || sshHost,
		model: router?.model || process.env.TOLLGATE_ROUTER_MODEL || 'unknown',
		luciUrl,
		sshHost,
		sshUser: process.env.TOLLGATE_SSH_USER || process.env.TOLLGATE_LUCI_USER || router?.sshUser || 'root',
		arch: process.env.TOLLGATE_ROUTER_ARCH || router?.arch || 'aarch64_cortex-a53',
		wifiInterface: process.env.TOLLGATE_WIFI_INTERFACE || router?.wifiInterface || '',
		tollgateSsidPrefix: process.env.TOLLGATE_SSID_PREFIX || router?.tollgateSsidPrefix || 'TollGate-',
	};
}
