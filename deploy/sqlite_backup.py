# -*- coding: utf-8 -*-
"""SQLite online backup + integrity check + 空库哨兵。

为什么不能直接拷文件：factory-hub 一直在跑。运行中的 SQLite 直接 Copy-Item 可能
拷到撕裂的页（写入进行中、hot journal 未应用），拷出来的副本恢复不了还看不出来。
在线备份 API 会正确加读锁，产出的永远是一致的文件。

三个刻意的设计（都是踩过的坑）：
1. 先写 .tmp，成功才 os.replace 成正式名。否则磁盘满/进程被杀会在备份目录里留下
   一个 0 字节但文件名完全正常、时间戳还最新的"备份"——真出事时恢复的人正好会
   挑中它，而 0 字节库的 PRAGMA integrity_check 照样返回 ok，看不出问题。
2. 空库直接判失败。main.py 是 Base.metadata.create_all()，生产库一旦被误删/改名，
   应用重启就自动建出一个结构完整但全空的库；空库能通过 integrity_check，于是
   脚本每天报成功，14 天后保留策略把最后一份有数据的备份删掉，日志里全是 OK。
3. 校验不过不删文件，改名成 .corrupt 留着。校验不过基本意味着源库（生产库）已经
   有坏页，而这份副本正是那个坏库的页级快照，是事后 .recover 抢救数据的唯一材料。

输出全部走 stdout（PS 侧用 2>&1 一并收进日志），只用 ASCII —— 服务器控制台是 GBK。

Usage: python sqlite_backup.py <src.db> <dst.db>
Exit:  0=ok  1=failed  2=refused (empty database)
"""
import os
import sqlite3
import sys

BUSY_TIMEOUT_MS = 60000   # 源库被写事务占着时最多等 60s，别一撞锁就当天没备份


def main():
    if len(sys.argv) != 3:
        print("usage: sqlite_backup.py <src.db> <dst.db>")
        return 1

    src, dst = sys.argv[1], sys.argv[2]
    tmp = dst + ".tmp"
    for leftover in (tmp,):
        if os.path.exists(leftover):
            os.remove(leftover)

    try:
        s = sqlite3.connect(src, timeout=BUSY_TIMEOUT_MS / 1000.0)
        s.execute("PRAGMA busy_timeout=%d" % BUSY_TIMEOUT_MS)
        d = sqlite3.connect(tmp)
        try:
            with d:
                s.backup(d)
        finally:
            d.close()
            s.close()
    except Exception as e:
        if os.path.exists(tmp):
            os.remove(tmp)
        print("FAILED during backup: %s: %s" % (type(e).__name__, e))
        return 1

    # 校验 + 清点。没验证过的备份不算备份。
    try:
        v = sqlite3.connect(tmp)
        try:
            verdict = v.execute("PRAGMA integrity_check").fetchone()[0]
            items = v.execute("SELECT count(*) FROM stock_items").fetchone()[0]
            inbounds = v.execute("SELECT count(*) FROM factory_inbounds").fetchone()[0]
        finally:
            v.close()
    except Exception as e:
        os.remove(tmp)
        print("FAILED during verify: %s: %s" % (type(e).__name__, e))
        return 1

    if verdict != "ok":
        bad = dst + ".corrupt"
        os.replace(tmp, bad)
        print("CORRUPT integrity_check=%s -> kept as %s (source db is likely damaged; "
              "use this snapshot with sqlite3 .recover, do NOT delete)" % (verdict, bad))
        return 1

    if items == 0 and inbounds == 0:
        sus = dst + ".suspect-empty"
        os.replace(tmp, sus)
        print("REFUSED empty database (stock_items=0 inbounds=0) -> kept as %s . "
              "Refusing so the retention policy will NOT age out the last good backup. "
              "Check whether factory_hub.db was deleted/renamed and auto-recreated empty." % sus)
        return 2

    os.replace(tmp, dst)
    print("ok %s integrity=%s stock_items=%d inbounds=%d" % (dst, verdict, items, inbounds))
    return 0


if __name__ == "__main__":
    sys.exit(main())
