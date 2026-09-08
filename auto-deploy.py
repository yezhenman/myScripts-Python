# -*- coding: utf-8 -*-
"""
author：Yezhenman
time：2026/9/8
email：yezhenman@163.com
describe:批采项目打包 + SFTP 自动部署

注意：目标服务器账号为 SFTP-only，服务端禁止执行任何命令（不能远程解压、不能 reload nginx），
     因此部署全部依靠 SFTP 文件操作完成：
     上传到 dist_new_时间戳 -> 改名切换 -> 保留 dist_bak_时间戳 供回滚。
     实测服务器不支持并发连接（并发写入会超时卡死），所以上传固定单连接串行。
"""
import json
import os
import posixpath
import shutil
import stat
import subprocess
import sys
import time
from datetime import datetime

import paramiko

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "deploy_config.json")

# 项目本地路径 与 服务器站点目录名 的对应关系
PROJECTS = {
    "1": {"name": "fashion", "path": r"E:\批采系统代码\fashion\splist", "remote": "adm"},
    "2": {"name": "fashionUser", "path": r"E:\批采系统代码\fashionUser\splist", "remote": "user"},
    "3": {"name": "suppliers", "path": r"E:\批采系统代码\suppliers\splist", "remote": "supply"},
    "4": {"name": "sale", "path": r"E:\批采系统代码\sale\splist", "remote": "sale"},
}

BACKUP_KEEP = 1  # 服务器上保留几份历史备份
UPLOAD_RETRY = 3  # 单个文件上传失败重试次数
LINE = "=" * 58


# --------------------------------------------------------------------------- 通用


def load_config():
    if not os.path.exists(CONFIG_FILE):
        sys.exit("找不到配置文件: %s" % CONFIG_FILE)
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def human(size):
    return "%.1fMB" % (size / 1024.0 / 1024.0)


def ask(prompt, valid=None):
    while True:
        val = input(prompt).strip()
        if valid is None or val in valid:
            return val
        print("    输入无效，请重新选择")


def pick_projects():
    print("\n可选项目:")
    for key, info in PROJECTS.items():
        print("  [%s] %-12s -> 服务器目录 %s" % (key, info["name"], info["remote"]))
    print("  [A] 全部")
    raw = input("\n请选择项目编号 (空格隔开): ").strip().upper()
    keys = list(PROJECTS.keys()) if raw == "A" else raw.split()
    picked = [PROJECTS[k] for k in keys if k in PROJECTS]
    for k in keys:
        if k not in PROJECTS:
            print("    忽略无效编号: %s" % k)
    return picked


def pick_server(config):
    print("\n请选择环境:")
    options = list(config.keys())
    for i, key in enumerate(options, 1):
        cfg = config[key]
        target = cfg.get("host") or "（未配置）"
        print("  [%d] %-8s %s" % (i, cfg.get("label", key), target))
    idx = ask("请输入选项: ", [str(i) for i in range(1, len(options) + 1)])
    key = options[int(idx) - 1]
    cfg = config[key]
    if not cfg.get("host"):
        sys.exit("环境 [%s] 尚未在 deploy_config.json 中配置服务器信息" % cfg.get("label", key))
    if key == "prod":
        print("\n!! 即将操作【正式环境】: %s" % cfg["host"])
        if input("确认请输入大写 YES: ").strip() != "YES":
            sys.exit("已取消")
    return key, cfg


# --------------------------------------------------------------------------- 打包


def build(project, build_cmd):
    print("\n%s\n打包: %s | %s\n%s" % (LINE, project["name"], build_cmd, LINE))
    dist_path = os.path.join(project["path"], "dist")
    if os.path.exists(dist_path):
        print(">>> 清理旧的 dist 目录")
        shutil.rmtree(dist_path)
    subprocess.run(build_cmd, cwd=project["path"], shell=True, check=True)
    if not os.path.exists(os.path.join(dist_path, "index.html")):
        raise RuntimeError("打包结束但未生成 dist/index.html")
    print("--- 打包完成 ---")


# --------------------------------------------------------------------------- SFTP


class Sftp:
    """单连接 SFTP 会话，自带断线重连"""

    def __init__(self, cfg):
        self.cfg = cfg
        self.transport = None
        self.client = None

    def connect(self):
        self.close()
        self.transport = paramiko.Transport((self.cfg["host"], int(self.cfg.get("port", 22))))
        self.transport.banner_timeout = 30
        self.transport.connect(username=self.cfg["user"], password=self.cfg["password"])
        self.transport.set_keepalive(30)
        self.client = paramiko.SFTPClient.from_transport(self.transport)
        self.client.get_channel().settimeout(120)

    def close(self):
        for obj in (self.client, self.transport):
            try:
                if obj:
                    obj.close()
            except Exception:
                pass
        self.client = None
        self.transport = None

    def exists(self, path):
        try:
            self.client.stat(path)
            return True
        except IOError:
            return False

    def mkdir_p(self, path):
        if self.exists(path):
            return
        parent = posixpath.dirname(path)
        if parent and parent != "/" and not self.exists(parent):
            self.mkdir_p(parent)
        self.client.mkdir(path)

    def rmtree(self, path):
        """递归删除远程目录（SFTP 无法执行 rm -rf，只能逐个删）"""
        for attr in self.client.listdir_attr(path):
            child = posixpath.join(path, attr.filename)
            if stat.S_ISDIR(attr.st_mode):
                self.rmtree(child)
            else:
                self.client.remove(child)
        self.client.rmdir(path)

    def put(self, local, remote):
        last = None
        for attempt in range(1, UPLOAD_RETRY + 1):
            try:
                self.client.put(local, remote, confirm=True)
                return
            except Exception as e:
                last = e
                print("\n    上传失败(%d/%d) %s: %s" % (attempt, UPLOAD_RETRY, posixpath.basename(remote), e))
                if attempt < UPLOAD_RETRY:
                    time.sleep(2)
                    try:
                        self.connect()
                    except Exception as ce:
                        print("    重连失败: %s" % ce)
        raise RuntimeError("文件上传多次失败: %s (%s)" % (remote, last))


def scan_local(dist_path):
    """返回 (相对目录列表, [(本地绝对路径, 相对路径, 大小)], 总大小)"""
    dirs, files, total = [], [], 0
    for root, sub_dirs, sub_files in os.walk(dist_path):
        rel_root = os.path.relpath(root, dist_path)
        for d in sub_dirs:
            rel = d if rel_root == "." else os.path.join(rel_root, d)
            dirs.append(rel.replace("\\", "/"))
        for f in sub_files:
            rel = f if rel_root == "." else os.path.join(rel_root, f)
            full = os.path.join(root, f)
            size = os.path.getsize(full)
            files.append((full, rel.replace("\\", "/"), size))
            total += size
    return sorted(dirs), files, total


# --------------------------------------------------------------------------- 部署


def clean_backups(sftp, site_dir, keep):
    backups = sorted([n for n in sftp.client.listdir(site_dir) if n.startswith("dist_bak_")])
    stale = [n for n in sftp.client.listdir(site_dir) if n.startswith("dist_new_")]
    for name in stale:
        print(">>> 清理上次残留的中间目录: %s" % name)
        sftp.rmtree(posixpath.join(site_dir, name))
    for name in backups[: max(0, len(backups) - keep)]:
        print(">>> 删除过期备份: %s" % name)
        sftp.rmtree(posixpath.join(site_dir, name))


def deploy(sftp, project, server, stamp):
    dist_path = os.path.join(project["path"], "dist")
    if not os.path.exists(os.path.join(dist_path, "index.html")):
        raise RuntimeError("本地未找到 %s\\dist\\index.html，请先打包" % project["path"])

    site_dir = posixpath.join(server["root"], project["remote"])
    live = posixpath.join(site_dir, "dist")
    staging = posixpath.join(site_dir, "dist_new_%s" % stamp)
    backup = posixpath.join(site_dir, "dist_bak_%s" % stamp)

    mtime = datetime.fromtimestamp(os.path.getmtime(dist_path)).strftime("%m-%d %H:%M")
    dirs, files, total = scan_local(dist_path)
    print("\n%s\n部署: %s -> %s\n%s" % (LINE, project["name"], site_dir, LINE))
    print(">>> 本地 dist: %d 个文件 / %s / 生成于 %s" % (len(files), human(total), mtime))

    if not sftp.exists(site_dir):
        raise RuntimeError("服务器上不存在站点目录: %s" % site_dir)
    clean_backups(sftp, site_dir, BACKUP_KEEP)

    print(">>> 创建中间目录: %s" % posixpath.basename(staging))
    sftp.mkdir_p(staging)
    for rel in dirs:
        sftp.client.mkdir(posixpath.join(staging, rel))

    started = time.time()
    done_size = 0
    try:
        for i, (full, rel, size) in enumerate(files, 1):
            sftp.put(full, posixpath.join(staging, rel))
            done_size += size
            print("\r    [%3d%%] %d/%d  %s/%s  %-38s" % (
                done_size * 100 // max(total, 1), i, len(files),
                human(done_size), human(total), rel[-38:]), end="")
        print("\r    [100%%] %d/%d  %s  用时 %.1f 秒%s" % (
            len(files), len(files), human(total), time.time() - started, " " * 20))
    except Exception:
        print("\n>>> 上传中断，清理中间目录（线上版本未受影响）")
        try:
            sftp.rmtree(staging)
        except Exception as e:
            print("    清理失败，请手工删除 %s: %s" % (staging, e))
        raise

    print(">>> 切换版本")
    had_live = sftp.exists(live)
    if had_live:
        sftp.client.rename(live, backup)
    try:
        sftp.client.rename(staging, live)
    except Exception:
        if had_live:
            sftp.client.rename(backup, live)
            print("    切换失败，已还原为原版本")
        raise
    print("--- [成功] %s 已上线，备份: %s ---" % (project["name"], posixpath.basename(backup) if had_live else "无"))

    clean_backups(sftp, site_dir, BACKUP_KEEP)


# --------------------------------------------------------------------------- 回滚


def rollback(sftp, project, server):
    site_dir = posixpath.join(server["root"], project["remote"])
    live = posixpath.join(site_dir, "dist")
    backups = sorted([n for n in sftp.client.listdir(site_dir) if n.startswith("dist_bak_")], reverse=True)
    if not backups:
        print("--- %s 没有可用备份 ---" % project["name"])
        return

    print("\n%s 可用备份:" % project["name"])
    for i, name in enumerate(backups, 1):
        print("  [%d] %s" % (i, name))
    idx = ask("请选择要恢复的备份: ", [str(i) for i in range(1, len(backups) + 1)])
    chosen = posixpath.join(site_dir, backups[int(idx) - 1])

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if sftp.exists(live):
        sftp.client.rename(live, posixpath.join(site_dir, "dist_bak_%s" % stamp))
    sftp.client.rename(chosen, live)
    print("--- [成功] %s 已回滚到 %s ---" % (project["name"], backups[int(idx) - 1]))


# --------------------------------------------------------------------------- 入口


def main():
    print(LINE)
    print("   批采前端：打包 + SFTP 部署 + 回滚")
    print(LINE)
    print("  [1] 打包并部署")
    print("  [2] 仅打包")
    print("  [3] 仅部署（使用现有 dist）")
    print("  [4] 回滚到历史备份")
    action = ask("\n请选择操作: ", ["1", "2", "3", "4"])

    config = load_config()
    projects = pick_projects()
    if not projects:
        sys.exit("未选择任何项目")

    server = None
    if action != "2":
        _, server = pick_server(config)
    build_cmd = None
    if action in ("1", "2"):
        if server:
            build_cmd = server["build_cmd"]
        else:
            print("\n请选择打包环境:")
            print("  [1] 测试 (npm run build:dev)")
            print("  [2] 生产 (npm run build:prod)")
            build_cmd = "npm run build:dev" if ask("请输入选项: ", ["1", "2"]) == "1" else "npm run build:prod"
        print("\n打包命令: %s" % build_cmd)
        typed = input("直接回车使用，或输入其它命令: ").strip()
        if typed:
            build_cmd = typed

    sftp = None
    if action != "2":
        print("\n>>> 连接 %s ..." % server["host"])
        sftp = Sftp(server)
        sftp.connect()
        print("--- 连接成功 ---")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    succeeded, failed = [], []
    try:
        for project in projects:
            try:
                if action in ("1", "2"):
                    build(project, build_cmd)
                if action in ("1", "3"):
                    deploy(sftp, project, server, stamp)
                if action == "4":
                    rollback(sftp, project, server)
                succeeded.append(project["name"])
            except subprocess.CalledProcessError:
                failed.append(project["name"])
                print("--- [失败] %s 打包出错 ---" % project["name"])
            except Exception as e:
                failed.append(project["name"])
                print("--- [失败] %s: %s ---" % (project["name"], e))
    finally:
        if sftp:
            sftp.close()

    print("\n%s" % LINE)
    print("成功: %s" % ("、".join(succeeded) if succeeded else "无"))
    if failed:
        print("失败: %s" % "、".join(failed))
    input("按回车键退出...")


if __name__ == "__main__":
    main()
