"""
author：Yezhenman
time：2026/8/20
email：yezhenman@163.com
describe:批采项目 Git 工具（同步测试分支 / 合并正式分支 / 推送正式分支）
"""
import os
import subprocess

# 配置项目列表（与 auto-build.py 保持一致）
PROJECTS = {
    "1": {
        "name": "fashion",
        "path": r"E:\批采系统代码\fashion\splist",
        "dev_branch": "fashion-develop",
        "prod_branch": "fashion-production",
    },
    "2": {
        "name": "fashionUser",
        "path": r"E:\批采系统代码\fashionUser\splist",
        "dev_branch": "fashionUser-develop",
        "prod_branch": "fashionUser-production",
    },
    "3": {
        "name": "suppliers",
        "path": r"E:\批采系统代码\suppliers\splist",
        "dev_branch": "suppliers-dev",
        "prod_branch": "suppliers-prod",
    },
    "4": {
        "name": "sale",
        "path": r"E:\批采系统代码\sale\splist",
        "dev_branch": "sale-develop",
        "prod_branch": "sale-production",
    },
}

DEFAULT_REMOTE = "origin"
DEFAULT_COMMIT_MSG = "fix: 代码同步"


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


def git_ok(result):
    return result.returncode == 0


def git_err(result, fallback="未知错误"):
    return (result.stderr or result.stdout or fallback).strip()


def is_git_repo(project_path):
    return os.path.isdir(os.path.join(project_path, ".git"))


def has_uncommitted_changes(project_path):
    result = run_git(["status", "--porcelain"], cwd=project_path, check=False)
    return bool(result.stdout.strip())


def get_current_branch(project_path):
    result = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=project_path)
    return result.stdout.strip()


def get_ahead_count(project_path, remote, branch):
    run_git(["fetch", remote], cwd=project_path, check=False)
    result = run_git(
        ["rev-list", "--count", f"{remote}/{branch}..HEAD"],
        cwd=project_path,
        check=False,
    )
    if result.returncode != 0:
        return None
    return int(result.stdout.strip() or 0)


def checkout_branch(project_path, branch):
    result = run_git(["checkout", branch], cwd=project_path, check=False)
    if git_ok(result):
        return True, ""
    return False, git_err(result)


def choose_remote():
    print("\n请选择推送远程:")
    print("[1] origin (默认)")
    print("[2] github")
    print("[3] 手动输入其他远程名")
    remote_choice = input("请输入选项 (1/2/3，回车默认 origin): ").strip()

    if remote_choice == "2":
        return "github"
    if remote_choice == "3":
        return input("请输入远程名: ").strip() or DEFAULT_REMOTE
    return DEFAULT_REMOTE


def select_projects():
    for key, info in PROJECTS.items():
        print(
            f"[{key}] {info['name']:<14} "
            f"{info['dev_branch']} -> {info['prod_branch']}"
        )
    print("[A] 全部处理")

    choice_input = input("\n请选择项目编号 (空格隔开): ").strip().upper()
    selected_keys = list(PROJECTS.keys()) if choice_input == "A" else choice_input.split()

    valid_keys = []
    for key in selected_keys:
        if key in PROJECTS:
            valid_keys.append(key)
        else:
            print(f"--- [错误]: 无效编号 {key} ---")
    return valid_keys


def ensure_project_ready(project):
    name = project["name"]
    path = project["path"]

    if not os.path.isdir(path):
        return f"--- [失败]: {name} 项目目录不存在 -> {path} ---"
    if not is_git_repo(path):
        return f"--- [失败]: {name} 不是 Git 仓库 ---"
    return ""


def print_summary(title, success_count, total):
    print(f"\n{'=' * 45}")
    print(f"{title}: 成功 {success_count} / 共 {total} 个")


def sync_dev_project(project, commit_message, remote):
    """提交测试分支并立刻推送"""
    name = project["name"]
    path = project["path"]
    dev_branch = project["dev_branch"]

    print(f"\n{'=' * 45}")
    print(f"同步测试分支: {name} | 远程: {remote}")

    err = ensure_project_ready(project)
    if err:
        print(err)
        return False

    try:
        branch = get_current_branch(path)
        print(f">>> 当前分支: {branch}")
        if branch != dev_branch:
            print(f">>> 提示: 配置的测试分支是 {dev_branch}，将按当前分支提交并推送")

        if commit_message:
            print(">>> 正在 add + commit (--no-verify)...")
            run_git(["add", "."], cwd=path)
            run_git(["commit", "-m", commit_message, "--no-verify"], cwd=path)
            print(f"    - 已提交: {commit_message}")
        else:
            print(">>> 工作区干净，无需提交")

        ahead = get_ahead_count(path, remote, branch)
        if ahead == 0:
            print(f"--- [跳过]: 本地没有需要推送到 {remote}/{branch} 的提交 ---")
            return True

        if ahead is not None:
            print(f">>> 待推送提交数: {ahead}")

        print(f">>> 正在推送到 {remote}/{branch}...")
        push_result = run_git(["push", "-u", remote, "HEAD"], cwd=path, check=False)
        if not git_ok(push_result):
            print(f"--- [失败]: 推送出错 ---\n    {git_err(push_result)}")
            return False

        print(f"--- [成功]: {name} 测试分支已提交并推送到 {remote} ---")
        return True

    except subprocess.CalledProcessError as e:
        err_text = (e.stderr or e.stdout or str(e)).strip()
        print(f"--- [失败]: {name} 同步出错 ---\n    {err_text}")
        return False


def push_branch_project(project, remote, branch_key, label):
    """只推送指定分支，不提交、不合并"""
    name = project["name"]
    path = project["path"]
    branch = project[branch_key]

    print(f"\n{'=' * 45}")
    print(f"推送{label}: {name} | {remote}/{branch}")

    err = ensure_project_ready(project)
    if err:
        print(err)
        return False

    if has_uncommitted_changes(path):
        print(f"--- [失败]: {name} 有未提交改动，请先用功能 1 提交并推送测试分支 ---")
        return False

    try:
        ok, checkout_err = checkout_branch(path, branch)
        if not ok:
            print(f"--- [失败]: 切换 {branch} 失败 ---\n    {checkout_err}")
            return False

        ahead = get_ahead_count(path, remote, branch)
        if ahead == 0:
            print(f"--- [跳过]: 本地没有需要推送到 {remote}/{branch} 的提交 ---")
            return True

        if ahead is not None:
            print(f">>> 待推送提交数: {ahead}")

        print(f">>> 正在推送到 {remote}/{branch}...")
        push_result = run_git(["push", "-u", remote, branch], cwd=path, check=False)
        if not git_ok(push_result):
            print(f"--- [失败]: 推送出错 ---\n    {git_err(push_result)}")
            return False

        print(f"--- [成功]: {name} 已推送到 {remote}/{branch} ---")
        return True

    except subprocess.CalledProcessError as e:
        err_text = (e.stderr or e.stdout or str(e)).strip()
        print(f"--- [失败]: {name} 推送出错 ---\n    {err_text}")
        return False


def merge_to_prod_project(project):
    """只把测试分支合并进正式分支，不推送"""
    name = project["name"]
    path = project["path"]
    dev_branch = project["dev_branch"]
    prod_branch = project["prod_branch"]

    print(f"\n{'=' * 45}")
    print(f"合并正式分支: {name} | {dev_branch} -> {prod_branch}")

    err = ensure_project_ready(project)
    if err:
        print(err)
        return False

    if has_uncommitted_changes(path):
        print(f"--- [失败]: {name} 有未提交改动，请先用功能 1 提交并推送测试分支 ---")
        return False

    try:
        print(f">>> 切换到正式分支: {prod_branch}")
        ok, checkout_err = checkout_branch(path, prod_branch)
        if not ok:
            print(f"--- [失败]: 切换正式分支失败 ---\n    {checkout_err}")
            return False

        merge_msg = f"merge: {dev_branch} into {prod_branch}"
        print(f">>> 正在合并 {dev_branch}（不推送）...")
        merge_result = run_git(
            ["merge", "--no-ff", "--no-verify", "-m", merge_msg, dev_branch],
            cwd=path,
            check=False,
        )
        if not git_ok(merge_result):
            print(
                f"--- [失败]: 合并冲突或合并失败，请到项目里手动处理 ---\n"
                f"    {git_err(merge_result)}"
            )
            return False

        if "Already up to date" in (merge_result.stdout or ""):
            print(">>> 正式分支已包含测试分支内容，无需新的合并提交")

        print(f"--- [成功]: {name} 已在本地将 {dev_branch} 合并到 {prod_branch} ---")
        print(">>> 当前停在正式分支，如需推送请使用功能 3")
        return True

    except subprocess.CalledProcessError as e:
        err_text = (e.stderr or e.stdout or str(e)).strip()
        print(f"--- [失败]: {name} 合并出错 ---\n    {err_text}")
        return False


def run_sync_dev_flow():
    """功能 1：每个项目单独写 commit，提交后立刻推送测试分支"""
    selected_keys = select_projects()
    if not selected_keys:
        return

    print("\n请为有改动的项目分别填写提交说明（回车使用默认）:")
    messages = {}
    for key in selected_keys:
        project = PROJECTS[key]
        name = project["name"]
        path = project["path"]
        err = ensure_project_ready(project)
        if err:
            print(err)
            messages[key] = ""
            continue

        if has_uncommitted_changes(path):
            msg = input(f"  [{name}] 提交说明: ").strip() or DEFAULT_COMMIT_MSG
            messages[key] = msg
            print(f"    -> 将提交: {msg}")
        else:
            messages[key] = ""
            print(f"  [{name}] 工作区干净，无需提交，有未推送提交时会直接推送")

    remote = choose_remote()
    print(f">>> 本次推送远程: {remote}")

    success_count = 0
    for key in selected_keys:
        if sync_dev_project(PROJECTS[key], messages.get(key, ""), remote):
            success_count += 1

    print_summary("测试分支同步完成", success_count, len(selected_keys))


def run_merge_flow():
    """功能 2：只合并到正式分支，不推送"""
    selected_keys = select_projects()
    if not selected_keys:
        return

    print("\n即将只做本地合并（不推送）:")
    for key in selected_keys:
        project = PROJECTS[key]
        print(f"  {project['name']}: {project['dev_branch']} -> {project['prod_branch']}")

    confirm = input("\n确认合并到正式分支? (Y/n): ").strip().upper()
    if confirm == "N":
        print("已取消合并")
        return

    success_count = 0
    for key in selected_keys:
        if merge_to_prod_project(PROJECTS[key]):
            success_count += 1

    print_summary("正式分支合并完成（未推送）", success_count, len(selected_keys))


def run_push_prod_flow():
    """功能 3：只推送正式分支"""
    selected_keys = select_projects()
    if not selected_keys:
        return

    remote = choose_remote()
    print(f">>> 本次推送远程: {remote}")

    success_count = 0
    for key in selected_keys:
        if push_branch_project(PROJECTS[key], remote, "prod_branch", "正式分支"):
            success_count += 1

    print_summary("正式分支推送完成", success_count, len(selected_keys))


def main():
    print("=" * 45)
    print("   批采项目 Git 批量工具")
    print("=" * 45)
    print("[1] 提交并推送测试分支（每个项目单独写 commit）")
    print("[2] 合并测试分支到正式分支（不推送）")
    print("[3] 推送正式分支")

    mode = input("\n请选择功能 (1/2/3): ").strip()
    flows = {
        "1": run_sync_dev_flow,
        "2": run_merge_flow,
        "3": run_push_prod_flow,
    }
    flow = flows.get(mode)
    if flow:
        flow()
    else:
        print("--- [错误]: 无效功能编号 ---")

    print(f"\n{'=' * 45}\n所有任务执行完毕！")
    input("按回车键退出...")


if __name__ == "__main__":
    main()
