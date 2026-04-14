import subprocess
import asyncio
import tempfile
import os
from pathlib import Path


def generate_keypair() -> tuple[str, str]:
    """Returns (private_key, public_key)"""
    private = subprocess.check_output(["wg", "genkey"]).decode().strip()
    public = subprocess.check_output(["wg", "pubkey"], input=private.encode()).decode().strip()
    return private, public


def generate_preshared_key() -> str:
    return subprocess.check_output(["wg", "genpsk"]).decode().strip()


def add_peer(public_key: str, preshared_key: str, ip: str, interface: str = "wg0") -> bool:
    try:
        # Write psk to a temp file inside the container via docker exec
        # then use it, then remove it
        psk_tmp = f"/tmp/psk_{public_key[:8]}.tmp"
        subprocess.check_call(
            ["docker", "exec", "amnezia-awg", "sh", "-c",
             f"echo '{preshared_key}' > {psk_tmp}"]
        )
        subprocess.check_call([
            "docker", "exec", "amnezia-awg",
            "wg", "set", interface, "peer", public_key,
            "preshared-key", psk_tmp,
            "allowed-ips", ip
        ])
        subprocess.check_call(
            ["docker", "exec", "amnezia-awg", "rm", "-f", psk_tmp]
        )
        subprocess.check_call(
            ["docker", "exec", "amnezia-awg", "wg-quick", "save", interface]
        )
        return True
    except Exception:
        return False


def remove_peer(public_key: str, interface: str = "wg0") -> bool:
    try:
        subprocess.check_call([
            "docker", "exec", "amnezia-awg",
            "wg", "set", interface, "peer", public_key, "remove"
        ])
        subprocess.check_call(
            ["docker", "exec", "amnezia-awg", "wg-quick", "save", interface]
        )
        return True
    except Exception:
        return False


def get_peers_stats(interface: str = "wg0") -> dict:
    try:
        output = subprocess.check_output(
            ["docker", "exec", "amnezia-awg", "wg", "show", interface, "dump"]
        ).decode()
        peers = {}
        lines = output.strip().split("\n")
        for line in lines[1:]:  # skip server line
            parts = line.split("\t")
            if len(parts) >= 7:
                pubkey = parts[0]
                peers[pubkey] = {
                    "endpoint": parts[2],
                    "latest_handshake": int(parts[4]) if parts[4] != "0" else None,
                    "rx": int(parts[5]),
                    "tx": int(parts[6]),
                }
        return peers
    except Exception:
        return {}


def get_server_params(config_path: str) -> dict:
    """
    Read AWG server config params.
    If config_path starts with 'docker:', reads from inside the container:
      e.g. 'docker:amnezia-awg:/opt/amnezia/awg/wg0.conf'
    Otherwise reads from local filesystem.
    """
    params = {}
    try:
        if config_path.startswith("docker:"):
            _, container, path = config_path.split(":", 2)
            content = subprocess.check_output(
                ["docker", "exec", container, "cat", path]
            ).decode()
            lines = content.splitlines()
        else:
            with open(config_path) as f:
                lines = f.readlines()

        for line in lines:
            line = line.strip()
            for key in ["Jc", "Jmin", "Jmax", "S1", "S2", "H1", "H2", "H3", "H4"]:
                if line.startswith(key + " = ") or line.startswith(key + "="):
                    val = line.split("=", 1)[1].strip()
                    params[key.lower()] = int(val)
    except Exception:
        pass
    return params


async def get_next_available_ip(db, subnet: str = "10.8.0") -> str:
    from sqlalchemy import select
    from models import Device
    result = await db.execute(select(Device.ip_address))
    used = {row[0].split("/")[0] for row in result.fetchall()}
    for i in range(2, 255):
        ip = f"{subnet}.{i}"
        if ip not in used:
            return f"{ip}/32"
    raise ValueError("No available IPs in subnet")


def generate_client_config(
    private_key: str,
    client_ip: str,
    server_public_key: str,
    server_endpoint: str,
    server_port: int,
    preshared_key: str,
    jc: int = 4,
    jmin: int = 40,
    jmax: int = 70,
    s1: int = 0,
    s2: int = 0,
    h1: int = 1,
    h2: int = 2,
    h3: int = 3,
    h4: int = 4,
    dns: str = "1.1.1.1",
) -> str:
    return f"""[Interface]
PrivateKey = {private_key}
Address = {client_ip}
DNS = {dns}
Jc = {jc}
Jmin = {jmin}
Jmax = {jmax}
S1 = {s1}
S2 = {s2}
H1 = {h1}
H2 = {h2}
H3 = {h3}
H4 = {h4}

[Peer]
PublicKey = {server_public_key}
PresharedKey = {preshared_key}
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = {server_endpoint}:{server_port}
PersistentKeepalive = 25
"""
