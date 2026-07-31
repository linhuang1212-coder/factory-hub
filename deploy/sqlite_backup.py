# -*- coding: utf-8 -*-
"""SQLite online backup + integrity check.

Why not just copy the file: factory-hub keeps running while this executes. A raw
file copy of a live SQLite database can capture a torn page set (write in flight,
hot journal not applied) and the copy silently becomes unrestorable. The online
backup API takes a proper read lock per step and always yields a consistent file.

Output is ASCII only -- the server console is GBK.

Usage: python sqlite_backup.py <src.db> <dst.db>
"""
import os
import sqlite3
import sys

if len(sys.argv) != 3:
    sys.stderr.write("usage: sqlite_backup.py <src.db> <dst.db>\n")
    sys.exit(2)

src, dst = sys.argv[1], sys.argv[2]

# Open the source read-write (not mode=ro) so SQLite can roll back a hot journal
# if one is present; a read-only handle would just error out in that case.
s = sqlite3.connect(src)
d = sqlite3.connect(dst)
try:
    with d:
        s.backup(d)
finally:
    d.close()
    s.close()

# A backup nobody verified is not a backup. Prove it opens and passes integrity
# check before we let the retention policy start aging out older copies.
v = sqlite3.connect(dst)
try:
    verdict = v.execute("PRAGMA integrity_check").fetchone()[0]
    items = v.execute("SELECT count(*) FROM stock_items").fetchone()[0]
    inbounds = v.execute("SELECT count(*) FROM factory_inbounds").fetchone()[0]
finally:
    v.close()

if verdict != "ok":
    os.remove(dst)
    sys.stderr.write("integrity_check FAILED: %s (backup deleted)\n" % verdict)
    sys.exit(1)

print("ok %s integrity=%s stock_items=%d inbounds=%d" % (dst, verdict, items, inbounds))
