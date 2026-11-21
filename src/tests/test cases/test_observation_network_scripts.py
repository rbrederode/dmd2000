"""Exercise setup decisions using mocked commands, never host network changes."""
import os
from pathlib import Path
import shlex
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]
COMMON = r'''
require_root() { :; }
record() { printf '<%s>' "$@" >> "$NETWORK_TEST_LOG"; printf '\n' >> "$NETWORK_TEST_LOG"; }
'''
MAC = r'''
uname() { printf 'Darwin\n'; }
networksetup() {
  printf 'Hardware Port: AX88179A\nDevice: en10\n\nHardware Port: Wi-Fi\nDevice: en0\n'
  if [[ ${MOCK_USB:-0} == 1 ]]; then printf '\nHardware Port: iPhone USB\nDevice: en8\n'; fi
  if [[ ${MOCK_TWO:-0} == 1 ]]; then printf '\nHardware Port: Other Ethernet\nDevice: en9\n'; fi
}
ifconfig() {
  if [[ $# -gt 1 ]]; then record ifconfig "$@"; return; fi
  printf 'inet 172.20.10.10 netmask 0xfffffff0\n'
  if [[ ${MOCK_LINK:-active} == active ]]; then printf 'status: active\n'; else printf 'status: inactive\n'; fi
}
route() { printf 'interface: %s\n' "${MOCK_UPLINK:-en0}"; }
pgrep() { return "${MOCK_SHARING:-1}"; }
pfctl() {
  record pfctl "$@"
  if [[ "$*" == '-sn' ]]; then
    [[ ${MOCK_HOOK:-yes} != yes ]] || printf 'nat-anchor "com.apple/*" all\n'
  elif [[ "$*" == '-s info' ]]; then
    printf 'Status: %s\n' "${MOCK_PF:-Disabled}"
  elif [[ "$*" == '-E' ]]; then
    printf 'pf enabled\nToken : 123\n'
  elif [[ "$*" == *' -f '* ]]; then
    record RULE "$(cat "${@: -1}")"
  fi
}
sysctl() { record sysctl "$@"; }
nc() { record nc "$@"; return "${MOCK_PORT_FAIL:-0}"; }
'''
PI = r'''
uname() { printf 'Linux\n'; }
ip() {
  if [[ "$*" == '-4 -o address show dev eth0' ]]; then
    printf '2: eth0 inet %s brd 192.168.50.255 scope global eth0\n' "${MOCK_ADDRESS:-192.168.50.2/24}"
  else printf 'default via 192.168.50.1 dev eth0\n'; fi
}
nmcli() {
  record nmcli "$@"
  if [[ "$*" == '-g GENERAL.CONNECTION device show eth0' ]]; then printf '%s\n' "${MOCK_CONNECTION:-Wired connection 1}"
  elif [[ "$*" == '-g ipv4.method connection show '* ]]; then printf '%s\n' "${MOCK_METHOD:-manual}"; fi
}
curl() { record curl "$@"; return "${MOCK_CURL_FAIL:-0}"; }
getent() { record getent "$@"; return "${MOCK_DNS_FAIL:-0}"; }
'''


def run_setup(tmp_path, script, mocks, args=(), **env):
    log = tmp_path / 'commands'
    source = shlex.quote(str(ROOT / 'src/config/rotation/scripts' / script))
    command = f'source {source}\n' + COMMON + mocks + '\nmain ' + ' '.join(shlex.quote(x) for x in args)
    result = subprocess.run(['bash', '-c', command], text=True, capture_output=True,
                            env={**os.environ, 'NETWORK_TEST_LOG': str(log), **env}, timeout=10)
    return result, log.read_text() if log.exists() else ''


def test_mac_sets_both_addresses_and_targeted_nat(tmp_path):
    result, log = run_setup(tmp_path, 'setup_mac.sh', MAC)
    assert result.returncode == 0, result.stderr
    assert '<ifconfig><en10><inet><192.168.50.1><netmask><255.255.255.0><alias>' in log
    assert '<ifconfig><en10><inet><192.168.0.250><netmask><255.255.255.0><alias>' in log
    assert 'nat on en0 inet from 192.168.50.2/32 to any -> (en0)' in log
    assert '<sysctl><-w><net.inet.ip.forwarding=1>' in log
    assert '<pfctl><-E>' in log
    assert '<pfctl><-f>' not in log  # Never replace the root ruleset.
    assert '<nc><-vz><-G><3><-s><192.168.0.250><192.168.0.63><23>' in log


def test_mac_detects_usb_uplink_without_selecting_it_as_lan(tmp_path):
    result, log = run_setup(tmp_path, 'setup_mac.sh', MAC, MOCK_USB='1', MOCK_UPLINK='en8', MOCK_PF='Enabled')
    assert result.returncode == 0, result.stderr
    assert 'Ethernet: en10; internet uplink: en8' in result.stdout
    assert 'nat on en8 inet from 192.168.50.2/32 to any -> (en8)' in log
    assert '<pfctl><-E>' not in log  # Repeated runs do not add enable references.


@pytest.mark.parametrize('env', [dict(MOCK_LINK='inactive'),dict(MOCK_UPLINK='en10'),
                                 dict(MOCK_TWO='1'),dict(MOCK_HOOK='no'),dict(MOCK_SHARING='0')])
def test_mac_preflight_failure_does_not_change_network(tmp_path, env):
    result, log = run_setup(tmp_path, 'setup_mac.sh', MAC, **env)
    assert result.returncode != 0
    assert 'ERROR:' in result.stderr
    assert '<ifconfig>' not in log and '<sysctl>' not in log
    assert '<pfctl><-E>' not in log
    assert '<-f>' not in log


def test_mac_explicit_adapter_resolves_ambiguity(tmp_path):
    result, log = run_setup(tmp_path, 'setup_mac.sh', MAC, args=('en10',), MOCK_TWO='1')
    assert result.returncode == 0, result.stderr
    assert '<ifconfig><en10>' in log and '<ifconfig><en9>' not in log


def test_pi_saves_and_applies_gateway_dns_without_changing_ip(tmp_path):
    result, log = run_setup(tmp_path, 'setup_pi.sh', PI)
    assert result.returncode == 0, result.stderr
    assert '<nmcli><connection><modify><Wired connection 1>' in log
    assert '<nmcli><device><modify><eth0>' in log
    assert log.count('<ipv4.gateway><192.168.50.1>') == 2
    assert log.count('<ipv4.dns><1.1.1.1 8.8.8.8>') == 2
    assert '<ipv4.addresses>' not in log
    assert '<ipv4.method><manual>' not in log
    assert '<connection><down>' not in log and '<device><disconnect>' not in log
    assert '<getent><ahostsv4><github.com>' in log


@pytest.mark.parametrize('env', [dict(MOCK_ADDRESS='192.168.50.3/24'),dict(MOCK_METHOD='auto'),dict(MOCK_CONNECTION='--')])
def test_pi_refuses_to_change_an_unexpected_existing_configuration(tmp_path, env):
    result, log = run_setup(tmp_path, 'setup_pi.sh', PI, **env)
    assert result.returncode != 0
    assert '<connection><modify>' not in log and '<device><modify>' not in log


def test_pi_distinguishes_dns_failure_after_successful_ip_test(tmp_path):
    result, log = run_setup(tmp_path, 'setup_pi.sh', PI, MOCK_DNS_FAIL='1')
    assert result.returncode != 0
    assert 'IP connectivity works but DNS failed' in result.stderr
    assert '<https://1.1.1.1>' in log
    assert '<https://github.com>' not in log
