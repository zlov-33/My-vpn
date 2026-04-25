"""
Subscription builder — assembles VLESS subscription responses for VPN clients.

Supports three formats:
  - json   : Full XRay/Sing-Box JSON config with routing rules (Happ, Hiddify)
  - v2ray  : Base64-encoded list of vless:// links (v2rayNG, v2rayN, NekoBox)
  - clash  : YAML proxy config (Clash Meta) — minimal implementation
"""
import base64
import json
import logging
from urllib.parse import urlparse, parse_qs

logger = logging.getLogger(__name__)

# Russian services that should bypass VPN (direct connection)
RU_DIRECT_DOMAINS = [
    "tbank", "t-bank", "tinkoff", "t-static",
    "vk.com", "vk.ru", "vkontakte", "ok.ru", "odnoklassniki", "okcdn", "mycdn.me",
    "mail.ru", "imgsmail", "cloud.mail",
    "gosuslugi", "gu-st.ru", "nalog", "mos.ru", "goskey", "goskey.ru",
    "ozon", "wildberries", "avito",
    "kinopoisk", "kpcdn", "dzen", "rutube", "okko", "ivi", "kion",
    "hh.ru", "headhunter", "2gis",
    "mts.ru", "megafon", "beeline", "tele2", "t2.ru",
    "magnit", "5ka", "perekrestok", "vkusvill", "auchan", "metro-cc",
    "samokat", "kuper", "megamarket", "detmir", "detskiimir",
    "alfabank", "alfa-bank", "alfaonline", "vtb", "psbank", "sberbank", "sber",
    "burgerking", "vkusnoitochka",
    "gazprom", "cbr.ru", "nspk", "rzd", "aeroflot", "pobeda",
    "rustore", "tutu", "cdek", "gismeteo", "pochta",
]


def _parse_vless_link(link: str) -> dict | None:
    """Parse a vless:// URL into a structured dict for XRay outbound."""
    try:
        parsed = urlparse(link)
        if parsed.scheme != "vless":
            return None

        user_id = parsed.username
        host = parsed.hostname
        port = parsed.port or 443
        params = parse_qs(parsed.query)

        def p(key: str, default: str = "") -> str:
            return params.get(key, [default])[0]

        return {
            "id": user_id,
            "host": host,
            "port": port,
            "flow": p("flow"),
            "security": p("security", "reality"),
            "sni": p("sni"),
            "fp": p("fp", "firefox"),
            "pbk": p("pbk"),
            "sid": p("sid"),
            "type": p("type", "tcp"),
            "remark": parsed.fragment or host,
        }
    except Exception as e:
        logger.debug(f"Failed to parse VLESS link: {e}")
        return None


def _build_outbound(parsed: dict, tag: str = "proxy") -> dict:
    """Build XRay outbound object from parsed VLESS link."""
    outbound = {
        "tag": tag,
        "protocol": "vless",
        "settings": {
            "vnext": [{
                "address": parsed["host"],
                "port": parsed["port"],
                "users": [{
                    "id": parsed["id"],
                    "encryption": "none",
                    "flow": parsed["flow"],
                }],
            }]
        },
        "streamSettings": {
            "network": parsed["type"],
            "security": parsed["security"],
        },
    }
    if parsed["security"] == "reality":
        outbound["streamSettings"]["realitySettings"] = {
            "fingerprint": parsed["fp"],
            "publicKey": parsed["pbk"],
            "serverName": parsed["sni"],
            "shortId": parsed["sid"],
        }
    elif parsed["security"] == "tls":
        outbound["streamSettings"]["tlsSettings"] = {
            "serverName": parsed["sni"],
            "fingerprint": parsed["fp"],
        }
    return outbound


def _build_routing_rules(sni_list: list[str]) -> list[dict]:
    """Build routing rules: RU domains → direct, SNI hosts → block, rest → proxy."""
    rules = []

    # Block SNI domains used as Reality targets (avoid loop)
    if sni_list:
        rules.append({
            "type": "field",
            "outboundTag": "block",
            "domain": [f"domain:{sni}" for sni in sni_list if sni],
        })

    # Russian services → direct
    rules.append({
        "type": "field",
        "outboundTag": "direct",
        "domain": [f"keyword:{d}" for d in RU_DIRECT_DOMAINS],
    })

    # Private IPs → direct
    rules.append({
        "type": "field",
        "outboundTag": "direct",
        "ip": ["geoip:private"],
    })

    return rules


def build_json_config(vless_links: list[str], sni_list: list[str] | None = None) -> dict:
    """
    Build a full XRay JSON config with routing.
    Compatible with Happ, Hiddify, v2rayN (JSON import).
    """
    outbounds = [
        {"tag": "direct", "protocol": "freedom"},
        {"tag": "block", "protocol": "blackhole"},
    ]

    for i, link in enumerate(vless_links):
        parsed = _parse_vless_link(link)
        if parsed:
            tag = "proxy" if i == 0 else f"proxy-{i}"
            outbounds.insert(i, _build_outbound(parsed, tag))

    config = {
        "remarks": "VPN Prime",
        "log": {"loglevel": "warning"},
        "dns": {
            "servers": ["1.1.1.1", "1.0.0.1"],
            "queryStrategy": "UseIP",
        },
        "inbounds": [
            {
                "tag": "socks",
                "port": 10808,
                "listen": "127.0.0.1",
                "protocol": "socks",
                "settings": {"auth": "noauth", "udp": True},
                "sniffing": {
                    "enabled": True,
                    "destOverride": ["http", "tls", "quic"],
                },
            },
            {
                "tag": "http",
                "port": 10809,
                "listen": "127.0.0.1",
                "protocol": "http",
            },
        ],
        "outbounds": outbounds,
        "routing": {
            "domainMatcher": "hybrid",
            "domainStrategy": "IPIfNonMatch",
            "rules": _build_routing_rules(sni_list or []),
        },
    }
    return config


def build_v2ray_subscription(vless_links: list[str]) -> bytes:
    """Return base64-encoded list of vless:// links (v2rayNG / v2rayN format)."""
    content = "\n".join(vless_links)
    return base64.b64encode(content.encode("utf-8"))


def build_clash_config(vless_links: list[str]) -> str:
    """Minimal Clash Meta YAML config."""
    proxies = []
    for i, link in enumerate(vless_links):
        parsed = _parse_vless_link(link)
        if not parsed:
            continue
        proxy = {
            "name": parsed["remark"] or f"VPN-{i+1}",
            "type": "vless",
            "server": parsed["host"],
            "port": parsed["port"],
            "uuid": parsed["id"],
            "network": parsed["type"],
            "tls": parsed["security"] in ("tls", "reality"),
            "flow": parsed["flow"] or None,
        }
        if parsed["security"] == "reality":
            proxy["reality-opts"] = {
                "public-key": parsed["pbk"],
                "short-id": parsed["sid"],
            }
            proxy["servername"] = parsed["sni"]
            proxy["client-fingerprint"] = parsed["fp"]
        proxies.append(proxy)

    proxy_names = [p["name"] for p in proxies]
    lines = [
        "mixed-port: 7890",
        "allow-lan: false",
        "mode: rule",
        "log-level: warning",
        "",
        "proxies:",
    ]
    for proxy in proxies:
        lines.append(f"  - name: \"{proxy['name']}\"")
        lines.append(f"    type: {proxy['type']}")
        lines.append(f"    server: {proxy['server']}")
        lines.append(f"    port: {proxy['port']}")
        lines.append(f"    uuid: {proxy['uuid']}")
        if proxy.get("flow"):
            lines.append(f"    flow: {proxy['flow']}")
        if proxy.get("tls"):
            lines.append("    tls: true")
        if proxy.get("servername"):
            lines.append(f"    servername: {proxy['servername']}")
        if proxy.get("client-fingerprint"):
            lines.append(f"    client-fingerprint: {proxy['client-fingerprint']}")
        if proxy.get("reality-opts"):
            ro = proxy["reality-opts"]
            lines.append("    reality-opts:")
            lines.append(f"      public-key: {ro['public-key']}")
            lines.append(f"      short-id: {ro['short-id']}")

    lines += [
        "",
        "proxy-groups:",
        f"  - name: Proxy",
        f"    type: select",
        f"    proxies: {json.dumps(proxy_names)}",
        "",
        "rules:",
        "  - GEOIP,CN,DIRECT",
        "  - MATCH,Proxy",
    ]
    return "\n".join(lines)


async def build_user_subscription(
    client,
    servers: list,
    fmt: str = "json",
) -> tuple[bytes | str, str]:
    """
    Collect VLESS links from all active servers for the client,
    build subscription in the requested format.

    Returns (content, media_type).
    """
    from vless_api import VlessApiClient
    from crypto import decrypt

    all_links: list[str] = []
    sni_list: list[str] = []

    for server in servers:
        if not server.is_active or not client.vless_username:
            continue
        api_pass = decrypt(server.api_pass_encrypted or "")
        vless = VlessApiClient(server.api_url, server.api_user, api_pass)
        try:
            links = await vless.get_subscription_links(client.vless_username)
            all_links.extend(links)
            if server.reality_sni:
                sni_list.append(server.reality_sni)
        except Exception as e:
            logger.warning(f"Server {server.name}: failed to get links: {e}")

    if not all_links:
        # Fallback to single-node from settings
        from config import settings
        from crypto import decrypt as dec
        vless = VlessApiClient(
            settings.vless_api_url, settings.vless_api_user, settings.vless_api_pass
        )
        try:
            all_links = await vless.get_subscription_links(client.vless_username)
        except Exception as e:
            logger.warning(f"Fallback API failed: {e}")

    if fmt == "v2ray":
        return build_v2ray_subscription(all_links), "text/plain; charset=utf-8"
    elif fmt == "clash":
        return build_clash_config(all_links), "text/yaml; charset=utf-8"
    else:
        config = build_json_config(all_links, sni_list)
        return json.dumps(config, ensure_ascii=False, indent=2), "application/json"
