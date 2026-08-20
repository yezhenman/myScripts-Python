"""
author：Yezhenman
time：2026/8/3 16:26
email：yezhenman@163.com
describe:托马斯项目快速打包
"""
import os
import subprocess
import shutil
from datetime import datetime

# 1. 配置项目列表
PROJECTS = {
    "1": {"name": "admin", "path": r"E:\VALORE\admin\splist"},
    "2": {"name": "user", "path": r"E:\VALORE\user\splist"},
}


def clean_old_files(project_path):

    """打包前的清场工作"""
    print(f">>> 正在清理旧文件...")
    dist_path = os.path.join(project_path, "dist")
    if os.path.exists(dist_path):
        shutil.rmtree(dist_path)
        print(f"    - 已删除旧的 dist 目录")

    old_zip = os.path.join(project_path, "dist.zip")
    if os.path.exists(old_zip):
        try:
            os.remove(old_zip)
            print(f"    - 已删除旧压缩包: dist.zip")
        except Exception as e:
            print(f"    - 删除旧压缩包失败: {e}")


def zip_dist(project_path):
    """压缩 dist 文件夹（包含文件夹本身）"""
    dist_path = os.path.join(project_path, "dist")
    if os.path.exists(dist_path):
        zip_base_name = os.path.join(project_path, "dist")
        print(f">>> 正在压缩: {dist_path} -> dist.zip")
        shutil.make_archive(
            base_name=zip_base_name,
            format='zip',
            root_dir=project_path,
            base_dir='dist'
        )
        print(f"--- [成功]: dist.zip 已生成 ---")
    else:
        print(f"--- [跳过]: 未发现 dist 文件夹 ---")


def main():
    print("=" * 45)
    print("   前端批量工具：清理 + 多环境打包 + 压缩")
    print("=" * 45)

    for key, info in PROJECTS.items():
        print(f"[{key}] {info['name']:<12}")
    print("[A] 全部处理")

    # 1. 选择项目
    choice_input = input("\n请选择项目编号 (空格隔开): ").strip().upper()
    selected_keys = PROJECTS.keys() if choice_input == 'A' else choice_input.split()

    # 2. 选择环境
    print("\n请选择打包环境:")
    print("[1] 开发环境 (npm run build:dev)")
    print("[2] 生产环境 (npm run build:prod)")
    print("[3] 手动输入其他命令")
    env_choice = input("请输入选项 (1/2/3): ").strip()

    if env_choice == "1":
        build_cmd = "npm run build:dev"
    elif env_choice == "2":
        build_cmd = "npm run build:prod"
    else:
        build_cmd = input("请输入完整打包命令: ").strip() or "npm run build"

    # 3. 执行任务
    for key in selected_keys:
        if key in PROJECTS:
            project = PROJECTS[key]
            print(f"\n{'=' * 45}")
            print(f"任务开始: {project['name']} | 命令: {build_cmd}")

            clean_old_files(project['path'])

            try:
                # 执行打包
                subprocess.run(build_cmd, cwd=project['path'], shell=True, check=True)
                # 压缩
                zip_dist(project['path'])

            except subprocess.CalledProcessError:
                print(f"--- [失败]: {project['name']} 打包出错 ---")
        else:
            print(f"--- [错误]: 无效编号 {key} ---")

    print(f"\n{'=' * 45}\n所有任务执行完毕！")
    input("按回车键退出...")


if __name__ == "__main__":
    main()