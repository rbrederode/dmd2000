#!/usr/bin/env bash
# Run after connecting the Ethernet switch and iPhone internet connection.
set -euo pipefail

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
require_root() { [[ $EUID -eq 0 ]] || die 'Run with sudo bash src/config/rotation/scripts/setup_mac.sh [Ethernet interface]'; }

main() {
  if [[ ${1:-} == --help || ${1:-} == -h ]]; then
    printf 'Usage: sudo bash src/config/rotation/scripts/setup_mac.sh [Ethernet interface]\nAutomatically selects the sole active wired adapter when omitted.\n'
    return
  fi
  [[ $# -le 1 ]] || die 'Supply at most one Ethernet interface.'
  [[ $(uname -s) == Darwin ]] || die 'This script is for macOS.'
  require_root
  local wired ethernet=${1:-} uplink anchors rules status candidate
  uplink=$(route -n get default | awk '/interface:/{print $2}')
  [[ -n "$uplink" ]] || die 'Connect the Mac to the iPhone first; no default internet route exists.'
  wired=$(networksetup -listallhardwareports | awk '
    /^Hardware Port:/ { port=$0 }
    /^Device:/ && port !~ /Wi-Fi|AirPort|Thunderbolt Bridge/ { print $2 }')
  if [[ -z "$ethernet" ]]; then
    while read -r candidate; do
      [[ -n "$candidate" && "$candidate" != "$uplink" ]] || continue
      if ifconfig "$candidate" | grep -q 'status: active'; then
        [[ -z "$ethernet" ]] || die 'Multiple wired adapters are active; supply the interface explicitly.'
        ethernet=$candidate
      fi
    done <<< "$wired"
  fi
  [[ -n "$ethernet" ]] || die 'No active wired adapter found. Connect and power the Ethernet switch first.'
  grep -Fxq "$ethernet" <<< "$wired" || die "$ethernet is not a listed wired adapter."
  ifconfig "$ethernet" | grep -q 'status: active' || die "$ethernet has no Ethernet link."
  [[ -n "$uplink" && "$uplink" != "$ethernet" ]] || die 'Connect the Mac to the iPhone first; internet must use a different interface.'
  [[ "$uplink" == en* ]] || die "Default route uses $uplink, possibly a VPN. Select the iPhone connection first."
  ifconfig "$uplink" | grep -q 'inet ' || die "$uplink has no IPv4 address."
  if pgrep -x InternetSharing >/dev/null; then
    die 'Turn off macOS Internet Sharing in System Settings before using this manual setup.'
  fi
  anchors=$(pfctl -sn)
  grep -Fq 'nat-anchor "com.apple/*"' <<< "$anchors" || die 'The system PF NAT hook is missing. No rules replaced; inspect sudo pfctl -sn before proceeding.'

  printf 'Ethernet: %s; internet uplink: %s\n' "$ethernet" "$uplink"
  ifconfig "$ethernet" inet 192.168.50.1 netmask 255.255.255.0 alias
  ifconfig "$ethernet" inet 192.168.0.250 netmask 255.255.255.0 alias
  rules=$(mktemp /private/tmp/dmd2000-nat.XXXXXX)
  printf 'nat on %s inet from 192.168.50.2/32 to any -> (%s)\n' "$uplink" "$uplink" > "$rules"
  if ! pfctl -n -a com.apple/dmd2000-sharing -f "$rules"; then
    rm -f "$rules"
    die 'NAT syntax check failed.'
  fi
  if ! pfctl -a com.apple/dmd2000-sharing -f "$rules"; then
    rm -f "$rules"
    die 'Could not load the Pi NAT rule.'
  fi
  rm -f "$rules"
  sysctl -w net.inet.ip.forwarding=1
  status=$(pfctl -s info)
  if ! grep -q '^Status: Enabled' <<< "$status"; then
    pfctl -E  # Keep the printed enable-reference token for optional cleanup.
  fi
  pfctl -a com.apple/dmd2000-sharing -sn
  printf '\nMac configured: 192.168.50.1/24 + 192.168.0.250/24. Checking device ports…\n'
  if ! nc -vz -G 3 -s 192.168.50.1 192.168.50.2 22; then
    printf 'WARNING: Pi SSH port unavailable; check its power, link and address.\n' >&2
  fi
  if ! nc -vz -G 3 -s 192.168.0.250 192.168.0.63 23; then
    printf 'WARNING: MD01 port unavailable; check its link/address or an existing controller session.\n' >&2
  fi
  printf '\nRun setup_pi.sh on the Pi next. Rerun this Mac script after a reboot, adapter reset or uplink change.\n'
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then main "$@"; fi
