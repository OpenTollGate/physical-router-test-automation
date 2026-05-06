import { ssh, shellQuote } from './ssh.mjs';
import { getRouter } from './inventory.mjs';

export function fileExists(path) {
	try { ssh(getRouter(), `test -f ${shellQuote(path)}`); return true; } catch { return false; }
}

export function readFile(path) {
	return ssh(getRouter(), `cat ${shellQuote(path)}`);
}

export function cleanupFiles(pattern) {
	ssh(getRouter(), `rm -f ${pattern}`);
}
