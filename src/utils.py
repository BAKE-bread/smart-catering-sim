# utils.py
"""
提供线程安全打印等通用工具。
"""

import threading
import time
import random
import numpy as np
from typing import List, Tuple, Set, Optional

print_lock = threading.Lock()

def safe_print(msg: str, color: str = "") -> None:
    """
    多线程安全打印，支持终端颜色。
    颜色选项: blue, green, yellow, red, purple, cyan
    """
    from config import SIMULATION_SPEED
    colors = {
        "blue": "\033[94m", "green": "\033[92m", "yellow": "\033[93m",
        "red": "\033[91m", "purple": "\033[95m", "cyan": "\033[96m", "end": "\033[0m"
    }
    c_start = colors.get(color, "")
    c_end = colors["end"] if color else ""
    with print_lock:
        print(f"{c_start}{msg}{c_end}")
        time.sleep(SIMULATION_SPEED)

def generate_chef_customer_groups(
    num_customers: int,
    num_chefs: int = 10,
    num_groups: Optional[int] = None,
    group_sizes: Optional[List[int]] = None,
    chefs_per_group: Tuple[int, int] = (1, 5),  # 每组厨师数量范围 [min, max]，大组取大值，小组取小值
    seed: Optional[int] = None,
    power_law_exponent: float = 1.5,            # 幂律指数：1.2~2.0，越大组大小分布越不均匀（少数大组+许多小组）
    max_group_ratio: float = 0.2,               # 单组顾客数上限占总顾客数的比例（20%），防止出现极端大组
    min_group_size: int = 1,                    # 每组最小顾客数（硬下限）
) -> List[Tuple[Set[int], Set[int]]]:
    """
    生成厨师-顾客分组，组大小服从幂律分布，厨师数量按组大小排名线性递减分配。
    
    核心机制：
    1. 组大小按幂律（Zipf）生成，超出上限的组会被“压缩”到上限附近（带随机扰动），
       然后多次迭代归一化，保证总和等于总顾客数。
    2. 组按大小降序排列后，排名靠前的组（大组）获得较多厨师，排名靠后的组（小组）获得较少厨师，
       厨师数量在 chefs_per_group 范围内线性插值，并添加随机抖动。
    3. 最终随机打乱组顺序，避免顺序偏见。
    
    参数：
        num_customers: 顾客总数（ID: 1..num_customers）
        num_chefs: 厨师总数（ID: 1..num_chefs）
        num_groups: 组数，若为None则自动估算（平均每组5~12人）
        group_sizes: 直接指定各组顾客数（优先级最高）
        chefs_per_group: (最小厨师数, 最大厨师数)
        seed: 随机种子
        power_law_exponent: 幂律指数，>1 时大组极少、小组极多
        max_group_ratio: 单组顾客数占总顾客数的最大比例（如0.2即20%）
        min_group_size: 每组最少顾客数（通常为1）
    """
    if seed is not None:
        random.seed(seed)
        np.random.seed(seed)

    # ---------- 1. 确定各组顾客数量（幂律分布 + 上限压缩） ----------
    if group_sizes is not None:
        sizes = list(group_sizes)
    else:
        # 1.1 确定组数
        if num_groups is None:
            # 经验范围平均每组5~15人，能产生足够的组间差异，又不至于组数过多
            target_avg = random.randint(5, 15)
            num_groups = max(1, num_customers // target_avg)
        else:
            if num_groups > num_customers:
                print(f"警告: 组数({num_groups})超过顾客数({num_customers})，调整为{num_customers}")
                num_groups = num_customers
            if num_groups < 1:
                num_groups = 1

        # 1.2 生成幂律分布权重（Zipf）
        ranks = np.arange(1, num_groups + 1)          # 排名从1到num_groups
        weights = 1.0 / (ranks ** power_law_exponent) # 指数越大，排名靠前的权重越大
        weights /= weights.sum()
        
        # 原始比例分配（浮点数）
        raw_sizes = weights * num_customers
        
        # 计算硬上限：最大顾客数 = 总顾客数 * max_group_ratio（但至少不低于 min_group_size）
        max_size = int(num_customers * max_group_ratio)
        if max_size < min_group_size:
            max_size = min_group_size
       
        # 1.3 压缩超出上限的组：对每个超标组，随机将其大小设为上限的50%~100%并添加小扰动
        compressed = False
        for i in range(len(raw_sizes)):
            if raw_sizes[i] > max_size:
                # 压缩比例0.5~1.0（避免全部卡在100%导致缺乏多样性）
                target = random.uniform(0.5, 1.0) * max_size
                # 再添加±10%的上限范围内的随机扰动，增加组大小差异
                target += random.uniform(-0.1 * max_size, 0.1 * max_size)
                raw_sizes[i] = max(min(target, max_size), min_group_size)
                compressed = True
        
        # 1.4 重新归一化，使总和恢复为num_customers
        #     由于压缩可能再次导致某些组超过上限，因此迭代3次（经验值）
        if compressed:
            for _ in range(3):
                raw_sizes = raw_sizes / raw_sizes.sum() * num_customers
                # 再次修剪超出部分（采用相同随机策略）
                for i in range(len(raw_sizes)):
                    if raw_sizes[i] > max_size:
                        target = random.uniform(0.5, 1.0) * max_size
                        target += random.uniform(-0.1 * max_size, 0.1 * max_size)
                        raw_sizes[i] = max(min(target, max_size), min_group_size)
        
        # 1.5 四舍五入取整，并保证每人至少 min_group_size
        sizes = np.maximum(np.round(raw_sizes), min_group_size).astype(int)
        
        # 1.6 修正因取整导致的总和偏差（逐组调整，优先调整大组或小组）
        diff = num_customers - sizes.sum()
        if diff != 0:
            order = np.argsort(sizes)               # 从小到大排序的索引
            if diff > 0:
                # 需要增加人数：从大到小依次增加（先加给当前还不到上限的大组）
                for i in order[::-1]:
                    if diff <= 0:
                        break
                    if sizes[i] < max_size:
                        sizes[i] += 1
                        diff -= 1
            else:
                # 需要减少人数：从小到大依次减少（先减给大于下限的小组）
                for i in order:
                    if diff >= 0:
                        break
                    if sizes[i] > min_group_size:
                        sizes[i] -= 1
                        diff += 1
        
        sizes = sizes.tolist()
    
    # ---------- 2. 对组按大小降序排序（大组在前，便于分配更多厨师） ----------
    sizes.sort(reverse=True)
    G = len(sizes)
    
    # ---------- 3. 随机打乱顾客ID顺序，避免顺序依赖 ----------
    customers = list(range(1, num_customers + 1))
    random.shuffle(customers)
    
    all_chefs = list(range(1, num_chefs + 1))
    min_chef, max_chef = chefs_per_group
    
    groups = []
    idx = 0
    # ---------- 4. 为每个组分配厨师：大组厨师多，小组厨师少（线性插值 + 随机抖动） ----------
    for rank, size in enumerate(sizes):
        group_cust = set(customers[idx:idx+size])
        idx += size
        
        # 计算该组在厨师数量上的比例因子（排名从0开始，排名0（最大组）ratio=1，最小组 ratio≈0）
        if G == 1:
            ratio = 1.0
        else:
            ratio = 1.0 - rank / (G - 1)   # rank=0 → ratio=1.0; rank=G-1 → ratio=0.0
        
        # 线性映射到 [min_chef, max_chef] 区间
        k_float = min_chef + (max_chef - min_chef) * ratio
        # 添加随机抖动：±0.5（经验值，使同一规模的组厨师数略有差异，避免完全一致）
        k_float += random.uniform(-0.5, 0.5)
        k = int(round(k_float))
        # 约束在合法范围 [min_chef, max_chef] 内
        k = max(min_chef, min(max_chef, k))
        
        chef_ids = set(random.sample(all_chefs, k))
        groups.append((chef_ids, group_cust))
    
    # ---------- 5. 最终随机打乱组顺序，避免顺序偏见 ----------
    random.shuffle(groups)
    return groups