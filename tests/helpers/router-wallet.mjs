import { ssh } from './ssh.mjs';
import { getRouter } from './inventory.mjs';

export function getWalletBalance() {
	const out = ssh(getRouter(), 'tollgate --json wallet balance');
	const data = JSON.parse(out);
	return data?.data?.balance_sats ?? 0;
}

export function getWalletInfo() {
	const out = ssh(getRouter(), 'tollgate --json wallet info');
	return JSON.parse(out);
}

export function drainViaCLI() {
	const out = ssh(getRouter(), 'tollgate --json wallet drain cashu');
	return JSON.parse(out);
}

export function fundViaCLI(token) {
	const escaped = token.replace(/'/g, "'\\''");
	const out = ssh(getRouter(), `tollgate --json wallet fund '${escaped}'`);
	return JSON.parse(out);
}
