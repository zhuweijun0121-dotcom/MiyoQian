#!/bin/bash
# 在上游代码基础上打补丁。
#
# 设计原则：
#   1. 上游代码为准，本脚本只做最小必要改动，不做功能增强。
#   2. 幂等 —— 每次同步后重复执行结果一致，不会叠加。
#   3. 每个补丁独立校验，任一失败立即退出（避免推残缺代码）。
#
# 当前补丁清单：空（上游已把所有需要的改动原生实现）。
#
# 已退役的补丁（上游已原生实现，不要再打）：
#   - config.py bbs read/like/share —— 上游 2026-11-04 6de8376 改成
#       for key in ("read", "like", "share"): bbs[key] = parse_bool(bbs.get(key, False))
#     用户开关不再被重置。旧的 setdefault 补丁反而因为找不到锚点，
#     让同步流水线从 2026-10-29 起连续失败 69 次，已删除。
#   - notifier.py telegram 自定义 api_url —— 上游 PR #16 (2026-09-04) 已原生支持，
#     且实现更规范（api_url 作为 base URL），配置项与 Web UI 均已提供。

set -u

echo "✅ 无待打补丁"
