#!/usr/bin/env bash
# Preserve the existing static IP; persist gateway and DNS together.
set -euo pipefail

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
require_root() { [[ $EUID -eq 0 ]] || die 'Run with sudo bash src/config/rotation/scripts/setup_pi.sh [Ethernet interface]'; }

main() {
  if [[ ${1:-} == --help || ${1:-} == -h ]]; then
    printf 'Usage: sudo bash src/config/rotation/scripts/setup_pi.sh [Ethernet interface, default eth0]\nRequires existing static 192.168.50.2/24; changes only gateway and DNS settings.\n'
    return
  fi
  [[ $# -le 1 ]] || die 'Supply at most one Ethernet interface.'
  [[ $(uname -s) == Linux ]] || die 'This script is for the Raspberry Pi.'
  require_root
  command -v nmcli >/dev/null || die 'NetworkManager/nmcli is required; no settings changed.'
  local ethernet=${1:-eth0} connection method
  ip -4 -o address show dev "$ethernet" | awk '{print $4}' | grep -Fxq '192.168.50.2/24' ||
    die "$ethernet must already have static 192.168.50.2/24. No address changed."
  connection=$(nmcli -g GENERAL.CONNECTION device show "$ethernet")
  [[ -n "$connection" && "$connection" != -- ]] || die "$ethernet has no active NetworkManager profile."
  method=$(nmcli -g ipv4.method connection show "$connection")
  [[ "$method" == manual ]] || die "Profile $connection is not static/manual; no settings changed."

  printf 'Preserving 192.168.50.2/24 on %s, profile %s\n' "$ethernet" "$connection"
  nmcli connection modify "$connection" ipv4.gateway 192.168.50.1 \
    ipv4.never-default no ipv4.dns '1.1.1.1 8.8.8.8' ipv4.ignore-auto-dns yes
  # Apply only these settings to the live connection, avoiding a disconnect or
  # reapplication of unrelated pending profile edits during an SSH session.
  nmcli device modify "$ethernet" ipv4.gateway 192.168.50.1 \
    ipv4.never-default no ipv4.dns '1.1.1.1 8.8.8.8' ipv4.ignore-auto-dns yes
  ip route
  nmcli -f IP4.ADDRESS,IP4.GATEWAY,IP4.DNS device show "$ethernet"
  curl -I --connect-timeout 5 --max-time 10 https://1.1.1.1 ||
    die 'IP internet test failed. Check the Mac script, forwarding, PF and iPhone connection.'
  getent ahostsv4 github.com || die 'IP connectivity works but DNS failed. Check DNS access/configuration.'
  curl -I --connect-timeout 5 --max-time 10 https://github.com ||
    die 'GitHub HTTPS test failed; inspect the curl error above.'
  printf '\nPi gateway and DNS are configured and saved. Its static address is unchanged.\n'
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then main "$@"; fi
