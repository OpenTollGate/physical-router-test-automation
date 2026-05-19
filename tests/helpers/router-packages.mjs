import { ssh, copyToRouter, remotePathFor, shellQuote } from './ssh.mjs';
import { runCommand } from './command.mjs';
import { getRouter } from './inventory.mjs';

export function installPackage(router = getRouter(), localPath, options = {}) {
	const remotePath = remotePathFor(localPath);
	copyToRouter(router, localPath, remotePath, { timeout: options.copyTimeout ?? 120000 });
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
	return ssh(router, `opkg remove ${shellQuote(packageName)}`, { check: false });
}

export function listInstalledPackages(router = getRouter(), filter) {
	const output = ssh(router, 'opkg list-installed');
	if (!filter) return output.split('\n').filter(Boolean);
	return output.split('\n').filter(line => line.includes(filter));
}

export function isPackageInstalled(router = getRouter(), packageName) {
	const packages = listInstalledPackages(router, packageName);
	return packages.some(line => line.startsWith(`${packageName} - `));
}
