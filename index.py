"""云函数入口，执行方法 index.main_handler。

云函数上传的 zip 包里 config.yaml 与本文件同目录，配置路径据此定位。
"""

import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from miyouqian.cli import main

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")


def main_handler(event, context):
    print("====== 米游签云函数任务开始 ======")
    if not os.path.exists(CONFIG_PATH):
        print(f"====== 未找到配置文件: {CONFIG_PATH} ======")
        print("====== 请确认 config.yaml 已随项目一并打包上传 ======")
        return 1
    try:
        exit_code = main(["--config", CONFIG_PATH, "run"])
        print(f"====== 任务执行完毕，退出码: {exit_code} ======")
        return exit_code
    except Exception as e:
        print(f"====== 任务运行异常: {str(e)} ======")
        raise e
