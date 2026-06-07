# global_state.py
"""
全局共享状态、锁、仿真数据收集容器。
其他模块通过导入本模块来访问和修改这些全局资源。
"""

import threading
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

# ================= 全局锁 =================
shared_knowledge_lock = threading.Lock()
customer_state_lock = threading.Lock()
simulation_data_lock = threading.Lock()

# ================= 共享知识库 =================
# 格式: {菜谱ID: (菜单ID, 最后更新时间戳)}
shared_knowledge: Dict[str, Tuple[str, float]] = {}

# ================= 顾客状态 =================
@dataclass
class CustomerState:
    candidates: List[Dict]      # 当前可能的菜单列表
    rounds: int = 0              # 已进行的轮数
    confirmed: bool = False      # 是否已确认订单
    canceled: bool = False       # 是否已取消
    final_menu_id: Optional[str] = None
    probe_success: bool = False  # 是否通过探针直接成功

# {顾客ID: CustomerState}
global_customer_states: Dict[int, CustomerState] = {}

# ================= 仿真数据收集 =================
simulation_data = {
    "customer_rounds": {},           # {customer_id: rounds}
    "chef_total_rounds": {},         # {chef_id: total_rounds}
    "chef_customers_served": {},     # {chef_id: customers_served}
    "chef_knowledge_growth": {},     # {chef_id: [(time, knowledge_size)]}
    "learning_effect": [],           # [(is_learned, rounds)]
    "customer_chef_mapping": {},     # {customer_id: [chef_ids]}
    "match_accuracy": [],            # [(customer_id, is_correct)]
    "probe_success_count": 0,
    "probe_total_count": 0,
    "noise_recovery_count": 0        # 记录系统触发容错机制的次数
}