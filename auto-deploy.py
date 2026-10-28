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

     用户端（fashionUser）正式环境分大陆站和香港站，两者在不同服务器上，
     打包产物也分开（dist-cn / dist-hk），避免互相覆盖。
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
# sites: 正式环境下需要分站发布的项目（目前只有用户端），每个站点自带服务器、打包命令和产物目录
PROJECTS = {
    "1": {"name": "fashion", "path": r"E:\批采系统代码\fashion\splist", "remote": "adm"},
    "2": {
        "name": "fashionUser",
        "path": r"E:\批采系统代码\fashionUser\splist",
        "remote": "user",
        "sites": [
            {"label": "大陆站", "server": "prod_cn", "remote": "user",
             "dist": "dist-cn", "build_cmd": "npm run build:prod:cn"},
            {"label": "香港站", "server": "prod_hk", "remote": "user",
             "dist": "dist-hk", "build_cmd": "npm run build:prod:hk"},
        ],
    },
    "3": {"name": "suppliers", "path": r"E:\批采系统代码\suppliers\splist", "remote": "supply"},
    "4": {"name": "sale", "path": r"E:\批采系统代码\sale\splist", "remote": "sale"},
}

# 环境 -> 默认服务器（不分站的项目走这个）
ENVS = {
    "1": {"key": "test", "label": "测试环境", "server": "test"},
    "2": {"key": "prod", "label": "正式环境", "server": "prod_cn"},
}

BACKUP_KEEP = 1  # 服务器上保留几份历史备份
UPLOAD_RETRY = 3  # 单个文件上传失败重试次数
# 本地打包 Node 堆上限（MB）。package.json 里 Jenkins 固定 1800，本地 Vite 易 OOM，在此覆盖。
BUILD_NODE_HEAP_MB = 4096
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


def pick_env():
    print("\n请选择环境:")
    for key, env in ENVS.items():
        print("  [%s] %s" % (key, env["label"]))
    return ENVS[ask("请输入选项: ", list(ENVS.keys()))]


def pick_sites(project):
    """正式环境下选择要发布的站点，可多选"""
    sites = project["sites"]
    print("\n%s 分站发布，请选择站点:" % project["name"])
    for i, site in enumerate(sites, 1):
        print("  [%d] %s" % (i, site["label"]))
    print("  [A] 全部")
    raw = input("请选择 (空格隔开): ").strip().upper()
    if raw == "A":
        return list(sites)
    picked = [sites[int(r) - 1] for r in raw.split() if r.isdigit() and 1 <= int(r) <= len(sites)]
    if not picked:
        sys.exit("未选择任何站点")
    return picked


def build_tasks(projects, env, config, need_server):
    """把「项目 × 站点」展开成部署任务；不分站的项目走环境默认服务器"""
    tasks = []
    for project in projects:
        sites = project.get("sites") if env["key"] == "prod" else None
        variants = pick_sites(project) if sites else [None]
        for site in variants:
            server_key = site["server"] if site else env["server"]
            server = config.get(server_key)
            if server is None:
                sys.exit("deploy_config.json 中缺少环境配置: %s" % server_key)
            if need_server and not server.get("host"):
                sys.exit("环境 [%s] 尚未在 deploy_config.json 中配置服务器信息"
                         % server.get("label", server_key))
            tasks.append({
                "project": project,
                "title": "%s (%s)" % (project["name"], site["label"]) if site else project["name"],
                "build_cmd": site["build_cmd"] if site else server["build_cmd"],
                "dist": site["dist"] if site else "dist",
                "remote": site["remote"] if site else project["remote"],
                "server_key": server_key,
                "server": server,
            })
    return tasks


def confirm_tasks(tasks, env, action):
    print("\n%s\n本次操作清单:" % LINE)
    for task in tasks:
        if action == "2":
            print("  %-24s 打包 %s -> %s" % (task["title"], task["build_cmd"], task["dist"]))
        else:
            server = task["server"]
            target = posixpath.join(server["root"], task["remote"])
            print("  %-24s %s %s:%s" % (task["title"], server["label"], server["host"], target))
    print(LINE)
    if env["key"] == "prod" and action != "2":
        print("!! 即将操作【正式环境】")
        if input("确认请输入大写 YES: ").strip() != "YES":
            sys.exit("已取消")


# --------------------------------------------------------------------------- 打包


def resolve_build_cmd(build_cmd, project_path):
    """展开 npm run，并把 Jenkins 固定的 vite 1800MB 堆上限改为本地值（仅改 vite 段，minify 仍 512）。"""
    prefix = "npm run "
    if not build_cmd.startswith(prefix):
        return build_cmd
    script_name = build_cmd[len(prefix):].strip()
    pkg_path = os.path.join(project_path, "package.json")
    if not os.path.exists(pkg_path):
        return build_cmd
    with open(pkg_path, "r", encoding="utf-8") as f:
        scripts = json.load(f).get("scripts", {})
    script_body = scripts.get(script_name)
    if not script_body:
        return build_cmd
    jenkins_heap = "--max-old-space-size=1800"
    local_heap = "--max-old-space-size=%d" % BUILD_NODE_HEAP_MB
    if jenkins_heap not in script_body:
        return script_body
    return script_body.replace(jenkins_heap, local_heap, 1)


def build(task):
    project, build_cmd = task["project"], task["build_cmd"]
    resolved_cmd = resolve_build_cmd(build_cmd, project["path"])
    print("\n%s\n打包: %s | %s\n%s" % (LINE, task["title"], build_cmd, LINE))
    if resolved_cmd != build_cmd:
        print(">>> 本地堆上限 %dMB，实际执行:\n    %s" % (BUILD_NODE_HEAP_MB, resolved_cmd))
    dist_path = os.path.join(project["path"], task["dist"])
    if os.path.exists(dist_path):
        print(">>> 清理旧的 %s 目录" % task["dist"])
        shutil.rmtree(dist_path)
    subprocess.run(resolved_cmd, cwd=project["path"], shell=True, check=True)
    if not os.path.exists(os.path.join(dist_path, "index.html")):
        raise RuntimeError("打包结束但未生成 %s/index.html" % task["dist"])
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


def deploy(sftp, task, stamp):
    project, server = task["project"], task["server"]
    dist_path = os.path.join(project["path"], task["dist"])
    if not os.path.exists(os.path.join(dist_path, "index.html")):
        raise RuntimeError("本地未找到 %s\\%s\\index.html，请先打包" % (project["path"], task["dist"]))

    site_dir = posixpath.join(server["root"], task["remote"])
    live = posixpath.join(site_dir, "dist")
    staging = posixpath.join(site_dir, "dist_new_%s" % stamp)
    backup = posixpath.join(site_dir, "dist_bak_%s" % stamp)

    mtime = datetime.fromtimestamp(os.path.getmtime(dist_path)).strftime("%m-%d %H:%M")
    dirs, files, total = scan_local(dist_path)
    print("\n%s\n部署: %s -> %s:%s\n%s" % (LINE, task["title"], server["host"], site_dir, LINE))
    print(">>> 本地 %s: %d 个文件 / %s / 生成于 %s" % (task["dist"], len(files), human(total), mtime))

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
    print("--- [成功] %s 已上线，备份: %s ---" % (task["title"], posixpath.basename(backup) if had_live else "无"))

    clean_backups(sftp, site_dir, BACKUP_KEEP)


# --------------------------------------------------------------------------- 回滚


def rollback(sftp, task):
    site_dir = posixpath.join(task["server"]["root"], task["remote"])
    live = posixpath.join(site_dir, "dist")
    backups = sorted([n for n in sftp.client.listdir(site_dir) if n.startswith("dist_bak_")], reverse=True)
    if not backups:
        print("--- %s 没有可用备份 ---" % task["title"])
        return

    print("\n%s 可用备份:" % task["title"])
    for i, name in enumerate(backups, 1):
        print("  [%d] %s" % (i, name))
    idx = ask("请选择要恢复的备份: ", [str(i) for i in range(1, len(backups) + 1)])
    chosen = posixpath.join(site_dir, backups[int(idx) - 1])

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if sftp.exists(live):
        sftp.client.rename(live, posixpath.join(site_dir, "dist_bak_%s" % stamp))
    sftp.client.rename(chosen, live)
    print("--- [成功] %s 已回滚到 %s ---" % (task["title"], backups[int(idx) - 1]))


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

    env = pick_env()
    tasks = build_tasks(projects, env, config, need_server=action != "2")
    if action in ("1", "2") and len(tasks) == 1:
        print("\n打包命令: %s" % tasks[0]["build_cmd"])
        typed = input("直接回车使用，或输入其它命令: ").strip()
        if typed:
            tasks[0]["build_cmd"] = typed
    confirm_tasks(tasks, env, action)

    sessions = {}

    def session_for(task):
        """同一台服务器只连一次；服务器不支持并发，连接始终串行使用"""
        key = task["server_key"]
        if key not in sessions:
            print("\n>>> 连接 %s (%s) ..." % (task["server"]["host"], task["server"]["label"]))
            sftp = Sftp(task["server"])
            sftp.connect()
            sessions[key] = sftp
            print("--- 连接成功 ---")
        return sessions[key]

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    succeeded, failed = [], []
    try:
        for task in tasks:
            try:
                if action in ("1", "2"):
                    build(task)
                if action in ("1", "3"):
                    deploy(session_for(task), task, stamp)
                if action == "4":
                    rollback(session_for(task), task)
                succeeded.append(task["title"])
            except subprocess.CalledProcessError:
                failed.append(task["title"])
                print("--- [失败] %s 打包出错 ---" % task["title"])
            except Exception as e:
                failed.append(task["title"])
                print("--- [失败] %s: %s ---" % (task["title"], e))
    finally:
        for sftp in sessions.values():
            sftp.close()

    print("\n%s" % LINE)
    print("成功: %s" % ("、".join(succeeded) if succeeded else "无"))
    if failed:
        print("失败: %s" % "、".join(failed))
    input("按回车键退出...")


if __name__ == "__main__":
    main()
