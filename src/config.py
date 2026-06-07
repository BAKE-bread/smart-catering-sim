# config.py
"""
仿真系统全局配置参数，用户可根据需要修改。
"""
from utils import generate_chef_customer_groups

# ================= 核心规模 =================
N = 200  # 总菜品数量（菜谱数 = 菜单数 = N）

# ================= 仿真控制 =================
SIMULATION_SPEED = 0.001      # 打印延迟(秒)，控制输出速度
CUSTOMER_ARRIVAL_MIN_DELAY = 0.05   # 顾客到达最小间隔(秒)
CUSTOMER_ARRIVAL_MAX_DELAY = 0.5    # 顾客到达最大间隔(秒)

# ================= 厨师学习与知识库 =================
ENABLE_CHEF_LEARNING = True         # 是否开启厨师学习功能
ENABLE_SHARED_KNOWLEDGE = True      # 是否在厨师间共享知识库
LOCAL_KNOWLEDGE_CAPACITY = 100      # 每个厨师本地LRU知识库容量（条目数），需确保本地缓存容量大于或等于 N 的一小半。
KNOWLEDGE_SYNC_DELAY_PROB = 0.1     # 访问全局知识库时的模拟延迟概率
KNOWLEDGE_SYNC_DELAY_MAX = 0.5      # 最大同步延迟(秒)（当前未实现精确延迟，仅做概率标记）

# ================= 厨师组‑顾客组拓扑关系 =================
# 格式: (厨师ID集合, 顾客ID集合)
# 每个元组表示: 这些厨师共同服务这些顾客
# 允许厨师重叠（一位厨师可属于多个组），但每个顾客应仅属于一个组。
# CHEF_CUSTOMER_GROUPS = [
#     ({1}, {1, 2}),              # 厨师1单独服务顾客1、2
#     ({2}, {3}),                 # 厨师2单独服务顾客3
#     ({3, 4}, {4, 5, 6, 7}),     # 厨师3、4共同服务顾客4～7
#     ({1, 4, 5}, {8, 9})         # 厨师1、4、5共同服务顾客8、9
# ]

CHEF_CUSTOMER_GROUPS = generate_chef_customer_groups(
    num_customers=100,
    num_chefs=10,
    num_groups=15,
)
print(f"生成了 {len(CHEF_CUSTOMER_GROUPS)} 个组")
# for i, (chefs, customers) in enumerate(CHEF_CUSTOMER_GROUPS[:3]):
#     print(f"组{i+1}: 厨师{chefs} -> 顾客{list(customers)[:5]}...")

# ================= 噪音与容错仿真测试 =================
ENABLE_CUSTOMER_NOISE = False      # 是否开启：顾客记错配料特征
CUSTOMER_NOISE_PROB = 0.05         # 顾客记错的概率（默认5%）

ENABLE_CHEF_NOISE = False           # 是否开启：厨师因同步延迟误答
CHEF_NOISE_PROB = 0.05             # 厨师误答的概率（默认5%）
