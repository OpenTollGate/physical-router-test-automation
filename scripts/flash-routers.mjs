#!/usr/bin/env node
import { existsSync } from 'fs';
import { runCommand } from '../tests/helpers/command.mjs';
import { copyToRouter, remotePathFor, shellQuote, ssh } from '../tests/helpers/ssh.mjs';

function usage() {
	console.log('Usage: TOLLGATE_ENABLE_SYSUPGRADE_FLASHING=true TOLLGATE_ETHERNET_INTERFACES=enx0,enx1 TOLLGATE_LUCI_PASSWORD=<password> scripts/flash-routers.mjs <firmware-image>');
	console.log('WARNING: this performs sysupgrade and can make a router unreachable. Prefer TOLLGATE_PACKAGE_PATH/package install for routine tests.');
}

function interfaceIp(interfaceName) {
	const result = runCommand('ip', ['addr', 'show', interfaceName], { check: false, timeout: 10000 });
	return result.stdout.match(/inet\s+([^/\s]+)/)?.[1] || '';
}

function gatewayForInterface(interfaceName) {
	const result = runCommand('ip', ['route', 'show', 'dev', interfaceName], { check: false, timeout: 10000 });
	return result.stdout.match(/default via ([^\s]+)/)?.[1] || '';
}

function flashRouter(router, imagePath) {
	const remotePath = remotePathFor(imagePath);
	copyToRouter(router, imagePath, remotePath, { timeout: 180000 });
	const wipeConfig = process.env.TOLLGATE_SYSUPGRADE_WIPE_CONFIG === 'true';
	const flags = wipeConfig ? '-n ' : '';
	ssh(router, `sysupgrade ${flags}${shellQuote(remotePath)}`, { check: false, timeout: 10000 });
}

const imagePath = process.argv[2] || process.env.TOLLGATE_FIRMWARE_IMAGE;
if (process.argv.includes('--help') || process.argv.includes('-h')) {
	usage();
	process.exit(0);
}
if (!imagePath || !existsSync(imagePath)) {
	usage();
	throw new Error('firmware image path is required and must exist');
}
if (process.env.TOLLGATE_ENABLE_SYSUPGRADE_FLASHING !== 'true') {
	usage();
	throw new Error('sysupgrade flashing is disabled by default; set TOLLGATE_ENABLE_SYSUPGRADE_FLASHING=true only when recovery is available');
}
const interfaces = (process.env.TOLLGATE_ETHERNET_INTERFACES || '').split(',').map(value => value.trim()).filter(Boolean);
if (!interfaces.length) throw new Error('TOLLGATE_ETHERNET_INTERFACES is required');

console.log(`Monitoring ${interfaces.join(', ')} for routers to flash`);
const previousIps = new Map(interfaces.map(name => [name, '']));
const flashed = new Set();

setInterval(() => {
	for (const interfaceName of interfaces) {
		const ip = interfaceIp(interfaceName);
		if (ip !== previousIps.get(interfaceName)) {
			previousIps.set(interfaceName, ip);
			if (!ip) {
				flashed.delete(interfaceName);
				console.log(`${interfaceName}: no address`);
				continue;
			}
			const routerIp = gatewayForInterface(interfaceName);
			if (!routerIp || flashed.has(interfaceName)) continue;
			console.log(`${interfaceName}: router ${routerIp} detected`);
			flashRouter({ id: interfaceName, sshHost: routerIp, sshUser: process.env.TOLLGATE_SSH_USER || 'root' }, imagePath);
			flashed.add(interfaceName);
		}
	}
}, 2000);
