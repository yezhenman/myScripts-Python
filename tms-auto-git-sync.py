"""
author：Yezhenman
time：2026/8/20
email：yezhenman@163.com
describe:托马斯项目 Git 批量同步（本地提交 + 推送 origin）
"""
import os
import subprocess

# 配置项目列表（与 tms-auto-build.py 保持一致）
PROJECTS = {
    "1": {"name": "admin", "path": r"E:\VALORE\admin\splist"},
    "2": {"name": "user", "path": r"E:\VALORE\user\splist"},
}

REMOTE = "origin"


def run_git(args, cwd, check=True):
    """执行 git 命令并返回 CompletedProcess"""
    return subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )


def is_git_repo(project_path):
    """判断目录是否为 git 仓库"""
    return os.path.isdir(os.path.join(project_path, ".git"))


def has_uncommitted_changes(project_path):
    """检查是否有未提交的改动"""
    result = run_git(["status", "--porcelain"], cwd=project_path, check=False)
    return bool(result.stdout.strip())


def get_ahead_count(project_path, remote, branch):
    """获取本地领先指定远程分支的提交数，无法比较时返回 None"""
    run_git(["fetch", remote], cwd=project_path, check=False)
    result = run_git(
        ["rev-list", "--count", f"{remote}/{branch}..HEAD"],
        cwd=project_path,
        check=False,
    )
    if result.returncode != 0:
        return None
    return int(result.stdout.strip() or 0)


def sync_git_project(project, commit_message):
    """单个项目：提交本地改动并推送到 origin"""
    name = project["name"]
    path = project["path"]

    print(f"\n{'=' * 45}")
    print(f"Git 同步: {name} | 远程: {REMOTE}")

    if not os.path.isdir(path):
        print(f"--- [失败]: 项目目录不存在 -> {path} ---")
        return False

    if not is_git_repo(path):
        print(f"--- [失败]: 不是 Git 仓库 ---")
        return False

    try:
        branch_result = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=path)
        branch = branch_result.stdout.strip()
        print(f">>> 当前分支: {branch}")

        if has_uncommitted_changes(path):
            print(">>> 检测到未提交改动，正在 add + commit (--no-verify)...")
            run_git(["add", "."], cwd=path)
            run_git(["commit", "-m", commit_message, "--no-verify"], cwd=path)
            print(f"    - 已提交: {commit_message}")
        else:
            print(">>> 工作区干净，无需提交")

        ahead = get_ahead_count(path, REMOTE, branch)
        if ahead == 0:
            print(f"--- [跳过]: 本地没有需要推送到 {REMOTE} 的提交 ---")
            return True

        if ahead is not None:
            print(f">>> 待推送到 {REMOTE} 的提交数: {ahead}")

        print(f">>> 正在推送到 {REMOTE}...")
        push_result = run_git(["push", "-u", REMOTE, "HEAD"], cwd=path, check=False)

        if push_result.returncode != 0:
            err = (push_result.stderr or push_result.stdout or "未知错误").strip()
            print(f"--- [失败]: 推送出错 ---\n    {err}")
            return False

        print(f"--- [成功]: {name} 已同步到 {REMOTE} ---")
        return True

    except subprocess.CalledProcessError as e:
        err = (e.stderr or e.stdout or str(e)).strip()
        print(f"--- [失败]: {name} Git 操作出错 ---\n    {err}")
        return False


def main():
    print("=" * 45)
    print("   托马斯项目 Git 批量同步工具")
    print("=" * 45)

    for key, info in PROJECTS.items():
        print(f"[{key}] {info['name']:<12}")
    print("[A] 全部同步")

    choice_input = input("\n请选择项目编号 (空格隔开): ").strip().upper()
    selected_keys = list(PROJECTS.keys()) if choice_input == "A" else choice_input.split()

    default_msg = "fix: 代码同步"
    commit_message = input("\n请输入提交说明 (回车使用默认): ").strip() or default_msg

    success_count = 0
    for key in selected_keys:
        if key in PROJECTS:
            if sync_git_project(PROJECTS[key], commit_message):
                success_count += 1
        else:
            print(f"--- [错误]: 无效编号 {key} ---")

    print(f"\n{'=' * 45}")
    print(f"Git 同步完成: 成功 {success_count} / 共 {len(selected_keys)} 个")
    input("\n按回车键退出...")


if __name__ == "__main__":
    main()
