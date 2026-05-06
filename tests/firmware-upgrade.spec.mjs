import { test, expect } from '@playwright/test';
import { getRouter } from './helpers/inventory.mjs';
import { ssh, copyToRouter, remotePathFor, shellQuote } from './helpers/ssh.mjs';
import { waitForRouterCommand } from './helpers/router-config.mjs';
import { isSafeForNetworkTests } from './helpers/network.mjs';
import { installPackage, listInstalledPackages } from './helpers/router-packages.mjs';

/**
 * Upgrade tests support two paths, but only package install is enabled by
 * default.
 *
 * 1. PACKAGE mode (default, safe): `opkg install` a .ipk package.
 *    This only replaces TollGate package contents and preserves router state:
 *    network config, Dropbear keys, WAN SSH access, LuCI/uhttpd settings,
 *    wallet/config files, and local lab passwords.
 *    Set TOLLGATE_PACKAGE_PATH to a .ipk file.
 *
 * 2. SYSUPGRADE mode (disabled by default): flash a full firmware image.
 *    This is not safe for normal test runs because the lab router has state
 *    that stock firmware may not reproduce: Dropbear authorized_keys, SSH from
 *    WAN, non-default LuCI port/listeners, custom LAN addressing, and TollGate
 *    config backups. Even without `-n`, sysupgrade can lose SSH if the image
 *    does not preserve or boot that config. Run only with an external recovery
 *    path and TOLLGATE_ENABLE_SYSUPGRADE_TESTS=true.
 *
 * Safety assertions prevent running without ethernet and verify SSH
 * recovery before declaring success.
 */

test.describe.configure({ retries: 0 });

test.describe('firmware upgrade', () => {
	test('installs tollgate package and verifies service', async () => {
		const packagePath = process.env.TOLLGATE_PACKAGE_PATH;
		test.skip(!packagePath, 'TOLLGATE_PACKAGE_PATH is required (path to .ipk)');
		expect(packagePath).toMatch(/\.ipk$/);
		test.skip(!isSafeForNetworkTests(), 'router must be reachable via ethernet');

		const router = getRouter();

		// Pre-flight: capture current state
		const versionBefore = listInstalledPackages(router, 'tollgate').join('\n');
		const arch = ssh(router, 'opkg print-architecture | head -2', { check: false });
		console.log(`Arch: ${arch}`);
		console.log(`Installed before: ${versionBefore}`);

		// Safety: verify SSH works before touching anything
		const preSsh = ssh(router, 'echo ssh-ok', { check: false });
		expect(preSsh).toContain('ssh-ok');

		// Install package. This is the supported upgrade path for routine test
		// runs because it cannot change bootloader, base network, Dropbear, or
		// firewall defaults the way a full firmware image can.
		const installResult = installPackage(router, packagePath, {
			check: false,
			timeout: 60000,
		});
		console.log(`Install result: ${installResult}`);
		// opkg returns 0 even with warnings; check for error patterns
		expect(installResult).not.toContain('Cannot install');

		// Restart the tollgate service
		const initScripts = ssh(router, 'ls /etc/init.d/tollgate*', { check: false }).trim().split('\n');
		for (const script of initScripts) {
			if (script) {
				ssh(router, `${script} restart`, { check: false, timeout: 30000 });
			}
		}

		// Wait for service to be ready
		await new Promise(r => setTimeout(r, 5000));
		const serviceReady = waitForRouterCommand(router, 'tollgate --json wallet balance', 30000);
		expect(serviceReady).toBe(true);

		// Verify SSH still works (config preserved)
		const postSsh = ssh(router, 'echo ssh-ok', { check: false });
		expect(postSsh).toContain('ssh-ok');

		// Verify network still functional
		const hasRoute = waitForRouterCommand(router, 'ip route get 1.1.1.1', 30000);
		expect(hasRoute).toBe(true);

		// Verify new version installed
		const versionAfter = listInstalledPackages(router, 'tollgate').join('\n');
		expect(versionAfter).toBeTruthy();
		console.log(`Installed after: ${versionAfter}`);
	});

	test('flashes firmware image and verifies router comes back online', async () => {
		test.skip(process.env.TOLLGATE_ENABLE_SYSUPGRADE_TESTS !== 'true', 'sysupgrade is disabled by default; enable only with external recovery available');
		const imagePath = process.env.TOLLGATE_FIRMWARE_IMAGE;
		test.skip(!imagePath, 'TOLLGATE_FIRMWARE_IMAGE is required (path to .img/.bin)');
		test.skip(!isSafeForNetworkTests(), 'router must be reachable via ethernet');

		const router = getRouter();

		// Pre-flight: verify SSH access before flashing
		const preSsh = ssh(router, 'echo ssh-ok', { check: false });
		expect(preSsh).toContain('ssh-ok');

		// Capture pre-upgrade info for diagnostics
		const versionBefore = ssh(router, 'cat /etc/openwrt_release | grep RELEASE_VERSION', { check: false });
		console.log(`Firmware before: ${versionBefore}`);

		// Copy firmware to router
		const remotePath = remotePathFor(imagePath);
		copyToRouter(router, imagePath, remotePath, { timeout: 180000 });

		// Verify file landed intact
		const fileSize = ssh(router, `ls -l ${shellQuote(remotePath)} | awk '{print $5}'`, { check: false });
		expect(Number.parseInt(fileSize, 10)).toBeGreaterThan(0);

		// Run sysupgrade WITHOUT -n to preserve config (SSH keys, network, password)
		// Router will reboot — command exits with error because connection drops, that's expected
		ssh(router, `sysupgrade ${shellQuote(remotePath)}`, { check: false, timeout: 10000 });

		// Wait for router to come back (sysupgrade takes ~2 minutes)
		const recovered = waitForRouterCommand(router, 'echo alive', 180000);
		expect(recovered).toBe(true);

		// Verify SSH still works with same credentials
		const postSsh = ssh(router, 'echo ssh-ok', { check: false });
		expect(postSsh).toContain('ssh-ok');

		// Verify basic services
		const hostname = ssh(router, 'hostname', { check: false });
		expect(hostname).toBeTruthy();

		// Verify network is functional
		const hasRoute = waitForRouterCommand(router, 'ip route get 1.1.1.1', 60000);
		expect(hasRoute).toBe(true);

		// Check new firmware version
		const versionAfter = ssh(router, 'cat /etc/openwrt_release | grep RELEASE_VERSION', { check: false });
		expect(versionAfter).toBeTruthy();
		console.log(`Firmware after: ${versionAfter}`);
	});
});
