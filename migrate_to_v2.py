"""
Migration script: AWG → VLESS-only architecture (v2 refactoring).

What it does:
  1. For every Client without vless_username — create VLESS user via API.
  2. Fill vless_sub_token (secrets.token_urlsafe(12)) for clients that lack it.
  3. Set traffic_limit_gb based on plan (lite=100, standard=500, family=0).
  4. Add new columns to DB (traffic_limit_gb, traffic_used_bytes, vless_sub_token)
     via raw ALTER TABLE — safe to run on existing SQLite DB.
  5. Drop old AWG columns from Server and Client tables.
  6. Drop the devices table entirely.

Usage:
  python migrate_to_v2.py --dry-run   # preview only, no writes
  python migrate_to_v2.py             # apply for real
"""
import asyncio
import argparse
import logging
import secrets
import sys
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

PLAN_TRAFFIC_GB = {
    "lite": 100,
    "standard": 500,
    "family": 0,
}


async def run_migration(dry_run: bool):
    from sqlalchemy import text
    from database import AsyncSessionLocal, engine
    from models import Client, Server
    from config import settings
    from vless_api import VlessApiClient

    logger.info(f"Starting migration (dry_run={dry_run})")

    # --- Step 1: Add missing columns to existing DB ---
    async with engine.begin() as conn:
        existing_cols = {}

        # Check which columns already exist in clients
        result = await conn.execute(text("PRAGMA table_info(clients)"))
        client_cols = {row[1] for row in result.fetchall()}

        # Check which columns already exist in servers
        result = await conn.execute(text("PRAGMA table_info(servers)"))
        server_cols = {row[1] for row in result.fetchall()}

        if not dry_run:
            # Add new Client columns if missing
            if "traffic_limit_gb" not in client_cols:
                await conn.execute(text("ALTER TABLE clients ADD COLUMN traffic_limit_gb INTEGER NOT NULL DEFAULT 500"))
                logger.info("Added clients.traffic_limit_gb")
            if "traffic_used_bytes" not in client_cols:
                await conn.execute(text("ALTER TABLE clients ADD COLUMN traffic_used_bytes INTEGER NOT NULL DEFAULT 0"))
                logger.info("Added clients.traffic_used_bytes")
            if "vless_sub_token" not in client_cols:
                await conn.execute(text("ALTER TABLE clients ADD COLUMN vless_sub_token VARCHAR(64)"))
                logger.info("Added clients.vless_sub_token")

            # Add new Server columns if missing
            if "api_url" not in server_cols:
                await conn.execute(text("ALTER TABLE servers ADD COLUMN api_url VARCHAR(512) NOT NULL DEFAULT ''"))
                logger.info("Added servers.api_url")
            if "api_user" not in server_cols:
                await conn.execute(text("ALTER TABLE servers ADD COLUMN api_user VARCHAR(128) NOT NULL DEFAULT 'admin'"))
                logger.info("Added servers.api_user")
            if "api_pass_encrypted" not in server_cols:
                await conn.execute(text("ALTER TABLE servers ADD COLUMN api_pass_encrypted TEXT"))
                logger.info("Added servers.api_pass_encrypted")
            if "reality_sni" not in server_cols:
                await conn.execute(text("ALTER TABLE servers ADD COLUMN reality_sni VARCHAR(256) NOT NULL DEFAULT ''"))
                logger.info("Added servers.reality_sni")
            if "priority" not in server_cols:
                await conn.execute(text("ALTER TABLE servers ADD COLUMN priority INTEGER NOT NULL DEFAULT 0"))
                logger.info("Added servers.priority")
            if "ip" not in server_cols:
                await conn.execute(text("ALTER TABLE servers ADD COLUMN ip VARCHAR(64) NOT NULL DEFAULT ''"))
                logger.info("Added servers.ip")
            if "location" not in server_cols:
                await conn.execute(text("ALTER TABLE servers ADD COLUMN location VARCHAR(128) NOT NULL DEFAULT ''"))
                logger.info("Added servers.location")
        else:
            logger.info("[DRY-RUN] Would ALTER TABLE to add new columns")

    # --- Step 2: Update each client ---
    async with AsyncSessionLocal() as db:
        from sqlalchemy import select
        result = await db.execute(select(Client))
        clients = result.scalars().all()

        vless = VlessApiClient(
            settings.vless_api_url,
            settings.vless_api_user,
            settings.vless_api_pass,
        )

        for client in clients:
            changes = []

            # Set traffic_limit_gb by plan
            expected_gb = PLAN_TRAFFIC_GB.get(client.plan, 500)
            if getattr(client, "traffic_limit_gb", None) != expected_gb:
                changes.append(f"traffic_limit_gb={expected_gb}")
                if not dry_run:
                    client.traffic_limit_gb = expected_gb

            # Generate sub token if missing
            if not getattr(client, "vless_sub_token", None):
                token = secrets.token_urlsafe(12)
                changes.append(f"vless_sub_token={token}")
                if not dry_run:
                    client.vless_sub_token = token

            # Create VLESS user if missing
            if not client.vless_username:
                import random
                import string
                slug = "".join(c if c.isalnum() else "_" for c in client.name.lower())
                suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
                vless_username = f"{slug}_{suffix}"
                expire_ts = int(client.expires_at.timestamp()) if client.expires_at else 0
                data_limit_gb = PLAN_TRAFFIC_GB.get(client.plan, 500)
                changes.append(f"create_vless_user={vless_username}")
                if not dry_run:
                    try:
                        await vless.create_user(
                            vless_username,
                            expire_ts,
                            data_limit_gb=data_limit_gb,
                        )
                        client.vless_username = vless_username
                        logger.info(f"  Created VLESS user: {vless_username}")
                    except Exception as e:
                        logger.warning(f"  Failed to create VLESS user for {client.name}: {e}")
            else:
                # Update data limit in VLESS if needed
                data_limit_gb = PLAN_TRAFFIC_GB.get(client.plan, 500)
                changes.append(f"update_vless_data_limit={data_limit_gb}GB")
                if not dry_run:
                    try:
                        await vless.update_user_data_limit(client.vless_username, data_limit_gb)
                    except Exception as e:
                        logger.warning(f"  Failed to update VLESS data limit for {client.name}: {e}")

            if changes:
                logger.info(f"Client [{client.id}] {client.name}: {', '.join(changes)}")

        if not dry_run:
            await db.commit()
            logger.info("Client updates committed.")

    # --- Step 3: Drop devices table ---
    async with engine.begin() as conn:
        result = await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='devices'")
        )
        if result.fetchone():
            logger.info("Dropping devices table...")
            if not dry_run:
                await conn.execute(text("DROP TABLE IF EXISTS devices"))
                logger.info("devices table dropped.")
            else:
                logger.info("[DRY-RUN] Would DROP TABLE devices")
        else:
            logger.info("devices table does not exist, skipping.")

    # SQLite doesn't support DROP COLUMN in older versions, so we note which old columns remain.
    logger.info(
        "Note: SQLite does not support DROP COLUMN. Old AWG columns in servers/clients "
        "remain but are ignored by the updated models. They will vanish if you recreate the DB."
    )

    logger.info("Migration complete.")


def main():
    parser = argparse.ArgumentParser(description="Migrate VPN panel DB to v2 (VLESS-only)")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no writes")
    args = parser.parse_args()

    asyncio.run(run_migration(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
