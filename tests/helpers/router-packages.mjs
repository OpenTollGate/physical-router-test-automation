import { ssh, copyToRouter, remotePathFor, shellQuote } from './ssh.mjs';
import { runCommand } from './command.mjs';
import { getRouter } from './inventory.mjs';

let _pmCache = null;

/**
 * Detect the package manager on the router.
 * OpenWRT 25.x uses apk; 24.x and earlier use opkg.
 * Cached per-session after first detection.
 */
export function detectPackageManager(router = getRouter()) {
	if (_pmCache) return _pmCache;
	const result = ssh(router, 'command -v apk >/dev/null 2>&1 && echo apk || echo opkg', { check: false });
	_pmCache = result.trim() === 'apk' ? 'apk' : 'opkg';
	return _pmCache;
}

/** Reset the package manager cache (for testing). */
export function _resetPackageManagerCache() {
	_pmCache = null;
}

export function installPackage(router = getRouter(), localPath, options = {}) {
	const remotePath = remotePathFor(localPath);
	copyToRouter(router, localPath, remotePath, { timeout: options.copyTimeout ?? 120000 });
	const pm = detectPackageManager(router);
	if (pm === 'apk') {
		// apk add: --allow-untrusted for unsigned test packages
		return ssh(router, `apk add --allow-untrusted ${shellQuote(remotePath)}; status=$?; rm -f ${shellQuote(remotePath)}; exit $status`, {
			check: options.check,
			timeout: options.timeout ?? 60000,
		});
	}
	const flags = options.forceOverwrite === false ? '' : '--force-overwrite ';
	return ssh(router, `opkg install ${flags}${shellQuote(remotePath)}; status=$?; rm -f ${shellQuote(remotePath)}; exit $status`, {
		check: options.check,
		timeout: options.timeout ?? 60000,
	});
}

export function installPackageFromUrl(router = getRouter(), url) {
	const filename = url.split('/').pop();
	const localPath = `/tmp/${filename}`;
	runCommand('curl', ['-fsSL', '-o', localPath, url], { timeout: 120000 });
	return installPackage(router, localPath);
}

export function removePackage(router = getRouter(), packageName) {
	const pm = detectPackageManager(router);
	if (pm === 'apk') {
		return ssh(router, `apk del ${shellQuote(packageName)}`, { check: false });
	}
	return ssh(router, `opkg remove ${shellQuote(packageName)}`, { check: false });
}

export function listInstalledPackages(router = getRouter(), filter) {
	const pm = detectPackageManager(router);
	const cmd = pm === 'apk' ? 'apk list --installed' : 'opkg list-installed';
	const output = ssh(router, cmd);
	if (!filter) return output.split('\n').filter(Boolean);
	return output.split('\n').filter(line => line.includes(filter));
}

export function isPackageInstalled(router = getRouter(), packageName) {
	const pm = detectPackageManager(router);
	if (pm === 'apk') {
		// apk info -e exits 0 if installed, prints package info; non-zero otherwise
		const result = ssh(router, `apk info -e ${shellQuote(packageName)} 2>/dev/null`, { check: false });
		return result.trim().length > 0 && result.includes(packageName);
	}
	const packages = listInstalledPackages(router, packageName);
	return packages.some(line => line.startsWith(`${packageName} - `));
}
