import { spawnSync } from 'child_process';

export function runCommand(command, args = [], options = {}) {
	const result = spawnSync(command, args, {
		encoding: options.encoding ?? 'utf8',
		input: options.input,
		timeout: options.timeout ?? 30000,
		env: { ...process.env, ...(options.env ?? {}) },
	});
	if (result.error) throw result.error;
	if (options.check !== false && result.status !== 0) {
		const rendered = [command, ...args].join(' ');
		throw new Error(`${rendered} failed with exit ${result.status}
stdout:
${result.stdout || ''}
stderr:
${result.stderr || ''}`);
	}
	return result;
}

export function requireEnv(name) {
	const value = process.env[name];
	if (!value) throw new Error(`${name} is required`);
	return value;
}
