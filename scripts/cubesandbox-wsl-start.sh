#!/usr/bin/env bash
set -euo pipefail

TOOLBOX_ROOT=/usr/local/services/cubetoolbox
NODE_IP=${CUBE_SANDBOX_NODE_IP:-192.168.34.1}
HTTP_PROXY_VALUE=${HTTP_PROXY:-http://127.0.0.1:11081}
HTTPS_PROXY_VALUE=${HTTPS_PROXY:-http://127.0.0.1:11081}
ALL_PROXY_VALUE=${ALL_PROXY:-socks5h://127.0.0.1:11080}
NO_PROXY_VALUE=${NO_PROXY:-localhost,127.0.0.1,::1}

for pid in $(pgrep -f "^${TOOLBOX_ROOT}/Cubelet/bin/cubelet" || true); do
  sudo kill "$pid" 2>/dev/null || true
done
for pid in $(pgrep -f "^${TOOLBOX_ROOT}/network-agent/bin/network-agent" || true); do
  sudo kill "$pid" 2>/dev/null || true
done
sleep 1

sudo ip link del eth2 2>/dev/null || true
sudo ip link add eth2 type dummy
sudo ip addr add 10.255.255.249/30 dev eth2
sudo ip link set eth2 up
sudo ip route replace default via 10.255.255.250 dev eth2 metric 10000
sudo ip neigh replace 10.255.255.250 lladdr 02:00:00:00:00:02 dev eth2 nud reachable

sudo tc qdisc del dev lo clsact 2>/dev/null || true
sudo ip rule del pref 1 ipproto tcp table 127 2>/dev/null || true
sudo ip rule del pref 1 ipproto udp table 127 2>/dev/null || true
sudo ip rule del pref 1 ipproto tcp table 128 2>/dev/null || true
sudo ip rule del pref 1 ipproto udp table 128 2>/dev/null || true

sudo mountpoint -q /sys/fs/bpf || sudo mount -t bpf bpf /sys/fs/bpf
sudo mkdir -p \
  /tmp/cube \
  /data/cube-shim \
  "${TOOLBOX_ROOT}/network-agent/state" \
  /data/log/network-agent \
  /data/log/Cubelet \
  /data/log/CubeShim \
  /data/log/CubeVmm \
  /var/log/cube-sandbox-one-click
sudo touch /data/cube-shim/snapshot

sudo sh -c "HTTP_PROXY='${HTTP_PROXY_VALUE}' HTTPS_PROXY='${HTTPS_PROXY_VALUE}' ALL_PROXY='${ALL_PROXY_VALUE}' NO_PROXY='${NO_PROXY_VALUE}' http_proxy='${HTTP_PROXY_VALUE}' https_proxy='${HTTPS_PROXY_VALUE}' all_proxy='${ALL_PROXY_VALUE}' no_proxy='${NO_PROXY_VALUE}' nohup ${TOOLBOX_ROOT}/network-agent/bin/network-agent --cubelet-config ${TOOLBOX_ROOT}/Cubelet/config/config.toml --state-dir ${TOOLBOX_ROOT}/network-agent/state >/var/log/cube-sandbox-one-click/network-agent.log 2>&1 &"
sleep 3
sudo ip neigh replace 10.255.255.250 lladdr 02:00:00:00:00:02 dev eth2 nud reachable
sudo sh -c "CUBE_SANDBOX_NODE_IP=${NODE_IP} HTTP_PROXY='${HTTP_PROXY_VALUE}' HTTPS_PROXY='${HTTPS_PROXY_VALUE}' ALL_PROXY='${ALL_PROXY_VALUE}' NO_PROXY='${NO_PROXY_VALUE}' http_proxy='${HTTP_PROXY_VALUE}' https_proxy='${HTTPS_PROXY_VALUE}' all_proxy='${ALL_PROXY_VALUE}' no_proxy='${NO_PROXY_VALUE}' nohup ${TOOLBOX_ROOT}/Cubelet/bin/cubelet --log-level debug --config ${TOOLBOX_ROOT}/Cubelet/config/config.toml --dynamic-conf-path ${TOOLBOX_ROOT}/Cubelet/dynamicconf/conf.yaml >/var/log/cube-sandbox-one-click/cubelet.log 2>&1 &"
sleep 4

sudo tc qdisc del dev lo clsact 2>/dev/null || true
sudo ip rule del pref 1 ipproto tcp table 127 2>/dev/null || true
sudo ip rule del pref 1 ipproto udp table 127 2>/dev/null || true
sudo ip rule del pref 1 ipproto tcp table 128 2>/dev/null || true
sudo ip rule del pref 1 ipproto udp table 128 2>/dev/null || true

sudo "${TOOLBOX_ROOT}/scripts/one-click/quickcheck.sh"
if [ -x "${TOOLBOX_ROOT}/scripts/one-click/seed-cubemaster-metrics.sh" ]; then
  sudo env CUBEMASTER_METRIC_INS_ID="${NODE_IP}" \
    "${TOOLBOX_ROOT}/scripts/one-click/seed-cubemaster-metrics.sh"
fi
