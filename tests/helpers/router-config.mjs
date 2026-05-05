import { ssh, shellQuote } from './ssh.mjs';
import { getRouter } from './inventory.mjs';

export function waitForRouterCommand(router = getRouter(), command = 'ip route get 1.1.1.1', timeoutMs = 30000) {
	const started = Date.now();
	while (Date.now() - started < timeoutMs) {
		try {
			ssh(router, command, { timeout: 15000 });
			return true;
		} catch {
			Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 2000);
		}
	}
	return false;
}

export function ensureWwanInterface(router = getRouter()) {
	const exists = ssh(router, '/sbin/uci get network.wwan', { check: false });
	if (exists) return false;
	ssh(router, ['/sbin/uci set network.wwan=interface', "/sbin/uci set network.wwan.proto='dhcp'", "/sbin/uci set network.wwan.metric='2048'", '/sbin/uci commit network'].join(' && '));
	return true;
}

export function configureStationInterfaces({ router = getRouter(), upstreamSsid = process.env.TOLLGATE_UPSTREAM_SSID, upstreamPassword = process.env.TOLLGATE_UPSTREAM_WIFI_PASSWORD, enabledRadio = 'radio1' } = {}) {
	if (!upstreamSsid) throw new Error('TOLLGATE_UPSTREAM_SSID is required');
	if (!upstreamPassword) throw new Error('TOLLGATE_UPSTREAM_WIFI_PASSWORD is required');
	const radio0Disabled = enabledRadio === 'radio0' ? '0' : '1';
	const radio1Disabled = enabledRadio === 'radio1' ? '0' : '1';
	ssh(router, [
		'/sbin/uci set wireless.wifinet0=wifi-iface',
		"/sbin/uci set wireless.wifinet0.device='radio0'",
		"/sbin/uci set wireless.wifinet0.network='wwan'",
		"/sbin/uci set wireless.wifinet0.mode='sta'",
		`/sbin/uci set wireless.wifinet0.ssid=${shellQuote(upstreamSsid)}`,
		`/sbin/uci set wireless.wifinet0.key=${shellQuote(upstreamPassword)}`,
		"/sbin/uci set wireless.wifinet0.encryption='sae'",
		`/sbin/uci set wireless.wifinet0.disabled='${radio0Disabled}'`,
		'/sbin/uci set wireless.wifinet1=wifi-iface',
		"/sbin/uci set wireless.wifinet1.device='radio1'",
		"/sbin/uci set wireless.wifinet1.network='wwan'",
		"/sbin/uci set wireless.wifinet1.mode='sta'",
		`/sbin/uci set wireless.wifinet1.ssid=${shellQuote(upstreamSsid)}`,
		`/sbin/uci set wireless.wifinet1.key=${shellQuote(upstreamPassword)}`,
		"/sbin/uci set wireless.wifinet1.encryption='sae'",
		`/sbin/uci set wireless.wifinet1.disabled='${radio1Disabled}'`,
		'/sbin/uci commit wireless',
	].join(' && '));
}

export function restartNetwork(router = getRouter()) {
	ssh(router, '/etc/init.d/network restart', { check: false, timeout: 5000 });
	return waitForRouterCommand(router, 'ip route get 1.1.1.1', 90000);
}

export function rebootRouter(router = getRouter()) {
	ssh(router, 'reboot', { check: false, timeout: 5000 });
}
