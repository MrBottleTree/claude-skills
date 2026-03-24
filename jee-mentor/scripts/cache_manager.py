#!/usr/bin/env python3
"""
JEE Mentor Cache Manager — SQLite-backed local database for web-fetched content.

Usage:
    cache_manager.py get <topic>              # Retrieve cached content for a topic
    cache_manager.py store <topic> <url>      # Store content for a topic (reads stdin for content)
    cache_manager.py list                     # List all cached topics
    cache_manager.py stats                    # Show cache statistics
    cache_manager.py purge <topic>            # Delete cache for a specific topic
    cache_manager.py clear                    # Clear all expired entries
    cache_manager.py clear-all               # Wipe the entire cache

Cache lives at: ~/.jee_mentor/cache.db
Entries expire after 30 days by default.
"""

import sys
import sqlite3
import json
import os
from pathlib import Path
from datetime import datetime, timedelta

DB_PATH = Path.home() / ".jee_mentor" / "cache.db"
EXPIRE_DAYS = 30


def get_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    _init_db(conn)
    return conn


def _init_db(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS topic_cache (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            topic       TEXT NOT NULL,
            subject     TEXT,
            url         TEXT,
            content     TEXT NOT NULL,
            source_type TEXT DEFAULT 'web',
            fetched_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at  TIMESTAMP,
            tags        TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_topic ON topic_cache(topic);
        CREATE INDEX IF NOT EXISTS idx_subject ON topic_cache(subject);
        CREATE INDEX IF NOT EXISTS idx_expires ON topic_cache(expires_at);

        CREATE TABLE IF NOT EXISTS problems_cache (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            topic       TEXT NOT NULL,
            subject     TEXT,
            difficulty  TEXT,
            source      TEXT,
            problem     TEXT NOT NULL,
            solution    TEXT,
            url         TEXT,
            fetched_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at  TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_prob_topic ON problems_cache(topic);

        CREATE TABLE IF NOT EXISTS session_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            topic       TEXT NOT NULL,
            subject     TEXT,
            started_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            checkpoint  INTEGER DEFAULT 0,
            notes       TEXT
        );
    """)
    conn.commit()


def cmd_get(topic):
    """Retrieve cached content for a topic."""
    conn = get_connection()
    now = datetime.utcnow().isoformat()
    rows = conn.execute(
        "SELECT * FROM topic_cache WHERE topic LIKE ? AND (expires_at IS NULL OR expires_at > ?) ORDER BY fetched_at DESC LIMIT 3",
        (f"%{topic}%", now)
    ).fetchall()

    if not rows:
        print(f"CACHE_MISS: No cached content for '{topic}'")
        return 1

    for row in rows:
        print(f"--- CACHED ENTRY (id={row['id']}) ---")
        print(f"Topic:      {row['topic']}")
        print(f"Subject:    {row['subject'] or 'N/A'}")
        print(f"URL:        {row['url'] or 'N/A'}")
        print(f"Fetched:    {row['fetched_at']}")
        print(f"Expires:    {row['expires_at'] or 'never'}")
        print(f"Content:\n{row['content']}\n")
    conn.close()
    return 0


def cmd_store(topic, url, subject=None, tags=None):
    """Store content from stdin for a topic."""
    content = sys.stdin.read().strip()
    if not content:
        print("ERROR: No content provided via stdin.")
        return 1

    expires_at = (datetime.utcnow() + timedelta(days=EXPIRE_DAYS)).isoformat()
    conn = get_connection()
    conn.execute(
        "INSERT INTO topic_cache (topic, subject, url, content, expires_at, tags) VALUES (?, ?, ?, ?, ?, ?)",
        (topic, subject, url, content, expires_at, tags)
    )
    conn.commit()
    row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    print(f"STORED: topic='{topic}' id={row_id} expires={expires_at}")
    return 0


def cmd_store_problem(topic, problem, solution=None, difficulty=None, source=None, url=None, subject=None):
    """Store a practice problem."""
    expires_at = (datetime.utcnow() + timedelta(days=EXPIRE_DAYS)).isoformat()
    conn = get_connection()
    conn.execute(
        "INSERT INTO problems_cache (topic, subject, difficulty, source, problem, solution, url, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (topic, subject, difficulty, source, problem, solution, url, expires_at)
    )
    conn.commit()
    row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    print(f"STORED_PROBLEM: topic='{topic}' difficulty='{difficulty}' id={row_id}")
    return 0


def cmd_get_problems(topic, difficulty=None, limit=5):
    """Retrieve cached problems for a topic."""
    conn = get_connection()
    now = datetime.utcnow().isoformat()
    if difficulty:
        rows = conn.execute(
            "SELECT * FROM problems_cache WHERE topic LIKE ? AND difficulty=? AND (expires_at IS NULL OR expires_at > ?) ORDER BY RANDOM() LIMIT ?",
            (f"%{topic}%", difficulty, now, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM problems_cache WHERE topic LIKE ? AND (expires_at IS NULL OR expires_at > ?) ORDER BY RANDOM() LIMIT ?",
            (f"%{topic}%", now, limit)
        ).fetchall()

    if not rows:
        print(f"CACHE_MISS: No cached problems for '{topic}'")
        return 1

    for row in rows:
        print(f"--- PROBLEM (id={row['id']}) ---")
        print(f"Topic:      {row['topic']}")
        print(f"Difficulty: {row['difficulty'] or 'N/A'}")
        print(f"Source:     {row['source'] or 'N/A'}")
        print(f"Problem:\n{row['problem']}")
        if row['solution']:
            print(f"Solution:\n{row['solution']}")
        print()
    conn.close()
    return 0


def cmd_list():
    """List all cached topics."""
    conn = get_connection()
    now = datetime.utcnow().isoformat()
    rows = conn.execute(
        "SELECT topic, subject, COUNT(*) as entries, MAX(fetched_at) as last_updated FROM topic_cache WHERE (expires_at IS NULL OR expires_at > ?) GROUP BY topic ORDER BY subject, topic",
        (now,)
    ).fetchall()

    prob_rows = conn.execute(
        "SELECT topic, subject, COUNT(*) as count FROM problems_cache WHERE (expires_at IS NULL OR expires_at > ?) GROUP BY topic ORDER BY topic",
        (now,)
    ).fetchall()

    print("=== Cached Topics ===")
    if rows:
        for row in rows:
            print(f"  [{row['subject'] or '?'}] {row['topic']} — {row['entries']} entries, last updated {row['last_updated']}")
    else:
        print("  (none)")

    print("\n=== Cached Problems ===")
    if prob_rows:
        for row in prob_rows:
            print(f"  [{row['subject'] or '?'}] {row['topic']} — {row['count']} problems")
    else:
        print("  (none)")

    conn.close()
    return 0


def cmd_stats():
    """Show cache statistics."""
    conn = get_connection()
    now = datetime.utcnow().isoformat()

    total = conn.execute("SELECT COUNT(*) FROM topic_cache").fetchone()[0]
    active = conn.execute("SELECT COUNT(*) FROM topic_cache WHERE (expires_at IS NULL OR expires_at > ?)", (now,)).fetchone()[0]
    probs = conn.execute("SELECT COUNT(*) FROM problems_cache WHERE (expires_at IS NULL OR expires_at > ?)", (now,)).fetchone()[0]
    size_bytes = os.path.getsize(str(DB_PATH)) if DB_PATH.exists() else 0

    print(f"Cache DB:        {DB_PATH}")
    print(f"Total entries:   {total}")
    print(f"Active entries:  {active}")
    print(f"Cached problems: {probs}")
    print(f"DB size:         {size_bytes / 1024:.1f} KB")
    conn.close()
    return 0


def cmd_purge(topic):
    """Delete cache for a specific topic."""
    conn = get_connection()
    n1 = conn.execute("DELETE FROM topic_cache WHERE topic LIKE ?", (f"%{topic}%",)).rowcount
    n2 = conn.execute("DELETE FROM problems_cache WHERE topic LIKE ?", (f"%{topic}%",)).rowcount
    conn.commit()
    conn.close()
    print(f"PURGED: {n1} content entries, {n2} problems for topic matching '{topic}'")
    return 0


def cmd_clear():
    """Remove all expired entries."""
    conn = get_connection()
    now = datetime.utcnow().isoformat()
    n1 = conn.execute("DELETE FROM topic_cache WHERE expires_at IS NOT NULL AND expires_at <= ?", (now,)).rowcount
    n2 = conn.execute("DELETE FROM problems_cache WHERE expires_at IS NOT NULL AND expires_at <= ?", (now,)).rowcount
    conn.commit()
    conn.close()
    print(f"CLEARED: {n1} expired content entries, {n2} expired problems")
    return 0


def cmd_clear_all():
    """Wipe all cached data."""
    conn = get_connection()
    conn.executescript("DELETE FROM topic_cache; DELETE FROM problems_cache; DELETE FROM session_log;")
    conn.commit()
    conn.close()
    print("CLEARED ALL: cache wiped")
    return 0


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 0

    cmd = args[0]

    if cmd == "get" and len(args) >= 2:
        return cmd_get(args[1])
    elif cmd == "store" and len(args) >= 3:
        subject = args[3] if len(args) > 3 else None
        return cmd_store(args[1], args[2], subject=subject)
    elif cmd == "store-problem" and len(args) >= 3:
        # store-problem <topic> <difficulty> [subject] [source] [url]
        topic, problem_text = args[1], args[2]
        difficulty = args[3] if len(args) > 3 else None
        subject = args[4] if len(args) > 4 else None
        source = args[5] if len(args) > 5 else None
        url = args[6] if len(args) > 6 else None
        return cmd_store_problem(topic, problem_text, difficulty=difficulty, subject=subject, source=source, url=url)
    elif cmd == "get-problems" and len(args) >= 2:
        difficulty = args[2] if len(args) > 2 else None
        limit = int(args[3]) if len(args) > 3 else 5
        return cmd_get_problems(args[1], difficulty=difficulty, limit=limit)
    elif cmd == "list":
        return cmd_list()
    elif cmd == "stats":
        return cmd_stats()
    elif cmd == "purge" and len(args) >= 2:
        return cmd_purge(args[1])
    elif cmd == "clear":
        return cmd_clear()
    elif cmd == "clear-all":
        return cmd_clear_all()
    else:
        print(__doc__)
        return 1


if __name__ == "__main__":
    sys.exit(main())
