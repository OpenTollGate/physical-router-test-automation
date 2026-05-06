import { runCommand, requireEnv } from './command.mjs';
import { getRouter } from './inventory.mjs';

function shellQuote(value) {
	return `'${String(value).replace(/'/g, `'\''`)}'`;
}

export function ssh(routerOrCommand, maybeCommand, options = {}) {
	const router = typeof maybeCommand === 'string' ? routerOrCommand : getRouter();
	const command = typeof maybeCommand === 'string' ? maybeCommand : routerOrCommand;
	const password = process.env.TOLLGATE_SSH_PASSWORD || process.env.TOLLGATE_LUCI_PASSWORD;
	if (!password) requireEnv('TOLLGATE_LUCI_PASSWORD');
	const args = [
		'-e', 'ssh',
		'-o', 'StrictHostKeyChecking=no',
		'-o', 'UserKnownHostsFile=/dev/null',
		'-o', `ConnectTimeout=${options.connectTimeout ?? 10}`,
		`${router.sshUser}@${router.sshHost}`,
		command,
	];
	return runCommand('sshpass', args, { timeout: options.timeout ?? 30000, env: { SSHPASS: password }, check: options.check }).stdout.trim();
}

export function copyToRouter(router, localPath, remotePath, options = {}) {
	const password = process.env.TOLLGATE_SSH_PASSWORD || process.env.TOLLGATE_LUCI_PASSWORD;
	if (!password) requireEnv('TOLLGATE_LUCI_PASSWORD');
	runCommand('sshpass', ['-e', 'scp', '-o', 'StrictHostKeyChecking=no', '-o', 'UserKnownHostsFile=/dev/null', localPath, `${router.sshUser}@${router.sshHost}:${remotePath}`], {
		timeout: options.timeout ?? 120000,
		env: { SSHPASS: password },
	});
}

export function remotePathFor(localPath) {
	return `/tmp/${localPath.split('/').pop()}`;
}

export { shellQuote };
