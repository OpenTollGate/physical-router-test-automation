import { ssh, copyToRouter, remotePathFor } from './ssh.mjs';
import { runCommand } from './command.mjs';
import { getRouter } from './inventory.mjs';

export function installPackage(router = getRouter(), localPath) {
	const remotePath = remotePathFor(localPath);
	copyToRouter(router, localPath, remotePath);
	return ssh(router, `opkg install ${remotePath} && rm -f ${remotePath}`);
}

export function installPackageFromUrl(router = getRouter(), url) {
	const filename = url.split('/').pop();
	const localPath = `/tmp/${filename}`;
	runCommand('curl', ['-fsSL', '-o', localPath, url], { timeout: 120000 });
	return installPackage(router, localPath);
}

export function removePackage(router = getRouter(), packageName) {
	return ssh(router, `opkg remove ${packageName}`, { check: false });
}

export function listInstalledPackages(router = getRouter(), filter) {
	const output = ssh(router, 'opkg list-installed');
	if (!filter) return output.split('\n').filter(Boolean);
	return output.split('\n').filter(line => line.includes(filter));
}

export function isPackageInstalled(router = getRouter(), packageName) {
	const result = ssh(router, `opkg list-installed | grep -q '^${packageName} '`, { check: false });
	return result !== undefined;
}
