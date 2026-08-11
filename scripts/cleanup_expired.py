from __future__ import annotations

import argparse
import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from google.cloud import firestore, storage


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="List or explicitly delete expired sessions")
    parser.add_argument("--apply", action="store_true", help="perform deletion; default is dry-run")
    parser.add_argument("--backend", choices=["local", "gcp"], default="local")
    parser.add_argument("--local-store-dir", type=Path, default=Path(".local_store"))
    parser.add_argument("--expiration-days", type=int, default=7)
    parser.add_argument("--project", default="")
    parser.add_argument("--database", default="(default)")
    parser.add_argument("--bucket", default="")
    return parser.parse_args()


def cleanup_local(args: argparse.Namespace) -> int:
    database_path = args.local_store_dir / "trial_opt.db"
    if not database_path.is_file():
        print(f"No local database at {database_path}; 0 sessions selected.")
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=args.expiration_days)
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            "SELECT session_id, created_at FROM sessions WHERE deleted = 0"
        ).fetchall()
        expired = [
            session_id
            for session_id, created_at in rows
            if datetime.fromisoformat(created_at) <= cutoff
        ]
        print(f"local expired sessions: {len(expired)}; apply={args.apply}")
        if args.apply and expired:
            placeholders = ",".join("?" for _ in expired)
            connection.execute(f"DELETE FROM events WHERE session_id IN ({placeholders})", expired)
            connection.execute(
                f"UPDATE sessions SET deleted = 1 WHERE session_id IN ({placeholders})",
                expired,
            )
            connection.commit()
    return len(expired)


async def cleanup_gcp(args: argparse.Namespace) -> int:
    if not args.project or not args.bucket:
        raise SystemExit("--project and --bucket are required for --backend gcp")
    client = firestore.AsyncClient(project=args.project, database=args.database)
    cutoff = datetime.now(UTC)
    query = (
        client.collection("sessions")
        .where("expires_at", "<=", cutoff)
        .where("deleted", "==", False)
    )
    expired = [snapshot async for snapshot in query.stream()]
    print(f"gcp expired sessions: {len(expired)}; apply={args.apply}")
    if args.apply:
        bucket = storage.Client(project=args.project).bucket(args.bucket)
        for snapshot in expired:
            async for event in snapshot.reference.collection("events").stream():
                await event.reference.delete()
            await snapshot.reference.update({"deleted": True, "deleted_at": datetime.now(UTC)})
            for blob in bucket.list_blobs(prefix=f"sessions/{snapshot.id}/"):
                blob.delete()
    await client.close()
    return len(expired)


def main() -> None:
    args = arguments()
    if args.expiration_days < 1:
        raise SystemExit("--expiration-days must be positive")
    count = cleanup_local(args) if args.backend == "local" else asyncio.run(cleanup_gcp(args))
    print(f"selected={count}; applied={args.apply}")


if __name__ == "__main__":
    main()
