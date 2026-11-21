# Moonhut observation network

Use the TL-SG1005LP Ethernet switch to connect the Mac's Dell Ethernet adapter,
the digitiser Pi and the MD01. Connect the Mac to the iPhone hotspot first.

| Device/interface | IPv4 address | Purpose |
| --- | --- | --- |
| Mac Ethernet | `192.168.50.1/24` | Pi connection and Pi internet gateway |
| Mac Ethernet additional address | `192.168.0.250/24` | MD01 connection |
| Pi Ethernet (`eth0`) | `192.168.50.2/24` | Existing static digitiser address, retained |
| MD01 | `192.168.0.63/24` | Existing controller address, retained |
| Pi default gateway | `192.168.50.1` | Mac forwarding/NAT to the iPhone |
| Pi DNS | `1.1.1.1`, `8.8.8.8` | Internet name resolution |

## What went wrong on 13 November 2026

- The Mac initially had only `192.168.50.1`. Without the additional
  `192.168.0.250` address, traffic for the MD01 followed the Wi-Fi/iPhone default
  route. The switch alone does not route between the two IP subnets.
- During Internet Sharing reconfiguration, the Mac lost its Ethernet IPv4
  addresses. Restoring only the Pi-subnet address left the MD01 unreachable.
- The Pi had a static address but no default gateway. The Mac also initially had
  IPv4 forwarding off, PF disabled and no working Pi NAT rule.
- A default route added using `ip route` was lost when NetworkManager reapplied
  DNS settings. Configuring the gateway and DNS together through NetworkManager
  restored access. The earlier device-level changes were temporary.
- A successful HTTPS request to `https://1.1.1.1` proved IP access worked, but
  `git pull` still failed until DNS and the default gateway were both working.
- Privileged diagnostics later showed three successful MD01 pings and a working
  TCP port 23, while an ordinary terminal still reported “No route to host”.
  Dish Manager also connected. This indicated an app/user-specific access issue;
  the precise policy responsible was not established. A failed ping from one
  terminal was therefore not sufficient evidence of a controller or cable fault.

## Run at the start of a session

Run these before starting observations/Dish Manager. The MD01 port check briefly
opens and closes a connection but sends no movement commands. The scripts do not
edit DMD2000 profiles or controller settings.

1. Power/connect the switch, Pi and MD01. Connect the Mac to the iPhone and confirm
   the Mac can access the internet. Leave macOS **Internet Sharing off**: the
   Mac script manages the Pi's NAT rule directly, preserving the static subnets.
2. From the repository directory **on the Mac**, run:

   ```bash
   sudo bash src/config/rotation/scripts/setup_mac.sh
   ```

   It detects the current IPv4 internet interface and the sole active wired
   adapter other than that uplink. If there are multiple wired adapters, supply
   the correct one explicitly (yesterday it was `en10`):

   ```bash
   sudo bash src/config/rotation/scripts/setup_mac.sh en10
   ```

   The Mac gets both Ethernet addresses. The script enables forwarding and PF,
   and installs NAT only for `192.168.50.2` in `com.apple/dmd2000-sharing`.
   It retains the existing root firewall rules and does not repeatedly acquire
   PF enable references when PF is already enabled. The printed interface names
   let you verify that the uplink is the iPhone Wi-Fi or USB connection.

3. From the repository directory **on the Pi**, run:

   ```bash
   sudo bash src/config/rotation/scripts/setup_pi.sh
   ```

   This requires the existing static/manual `192.168.50.2/24` configuration. It
   saves the gateway and DNS together in the active NetworkManager profile,
   then applies those settings to the live device without taking Ethernet down.
   It does not change the IP address, mask or IPv4 method. Tests check IP internet
   access, GitHub DNS and GitHub HTTPS. Once these pass, run `git pull` normally.

   If this new script is not on the Pi yet, copy it before running it, for example
   from the Mac repository directory:

   ```bash
   scp src/config/rotation/scripts/setup_pi.sh rbrederode@192.168.50.2:/tmp/dmd2000-setup-pi.sh
   ```

   Then on the Pi: `sudo bash /tmp/dmd2000-setup-pi.sh`.

The Pi gateway/DNS settings persist. Mac aliases, NAT and forwarding are runtime
settings: rerun the Mac script after a reboot, Ethernet reconfiguration or an
internet-interface change. Rerunning either script applies the same intended
settings. Keep the Mac awake and connected while the Pi needs internet.

## If a check fails

- **No wired link:** check switch power, adapter, cable and port lights. Automatic
  detection stops rather than guessing when multiple wired adapters are active.
- **Missing PF NAT hook:** the script stops if the active root rules do not expose
  `nat-anchor "com.apple/*"`. Collect `sudo pfctl -sn`; do not flush or replace the
  complete firewall ruleset just to add sharing. The hook was present yesterday.
- **ALTQ warnings:** “No ALTQ support in kernel” is not a NAT failure.
- **Pi IP test fails:** verify the Mac's iPhone connection, both scripts' output,
  `ip route` on the Pi and `sudo pfctl -a com.apple/dmd2000-sharing -sn` on the Mac.
- **IP works but DNS fails:** inspect `/etc/resolv.conf` and
  `nmcli -f IP4.ADDRESS,IP4.GATEWAY,IP4.DNS device show eth0` on the Pi.
- **MD01 fails only in some apps:** compare a normal Apple Terminal port test
  with the script's privileged test. Inspect Local Network permission for the
  app hosting the terminal or launching Dish Manager; Terminal.app's permission
  does not establish another app's permission. Do not reconfigure a working
  network solely because one app's ping reports “No route to host”.

To remove only the script's NAT rule, use
`sudo pfctl -a com.apple/dmd2000-sharing -F nat`. If the script enabled PF, keep
its printed token; `sudo pfctl -X TOKEN` releases that reference. Do not globally
disable PF or forwarding if another service still needs it.

References: [NetworkManager nmcli](https://networkmanager.pages.freedesktop.org/NetworkManager/NetworkManager/nmcli.html),
[gateway settings](https://networkmanager.pages.freedesktop.org/NetworkManager/NetworkManager/nm-settings-nmcli.html),
[Apple local network privacy](https://developer.apple.com/documentation/technotes/tn3179-understanding-local-network-privacy),
and the Mac's installed `man pfctl` / `man pf.conf`.
