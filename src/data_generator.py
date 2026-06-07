# data_generator.py
"""
智能生成满足约束的菜谱、菜单及二者之间的秘密映射。
"""

import math
import random
from typing import List, Dict, Tuple, Set

def generate_data(n: int) -> Tuple[List[Dict], List[Dict], Dict[str, str]]:
    """
    生成 n 道菜品的菜谱、菜单，以及菜谱→菜单的秘密映射。
    返回: (recipes, menus, secret_mapping)
    """
    bits = math.ceil(math.log2(n))
    core_ings = [f"[主材]核心_{i}" for i in range(bits)]
    noise_ings = [f"[辅料]配菜_{i}" for i in range(100)]
    step_templates = [
        "将{}洗净切好", "热锅凉油，放入{}爆香", "加入{}翻炒至断生",
        "加入适量{}调味", "大火收汁", "出锅装盘", "淋上{}提香",
        "加入{}炖煮10分钟", "将{}焯水备用"
    ]

    recipes: List[Dict] = []
    menus: List[Dict] = []
    secret_mapping: Dict[str, str] = {}

    for i in range(n):
        bin_str = format(i, f'0{bits}b')
        base_ings = [core_ings[j] for j, bit in enumerate(bin_str) if bit == '1']

        # 菜谱：主材 + 随机辅料
        r_ings = base_ings + random.sample(noise_ings, k=random.randint(4, 8))
        r_steps = [step.format(random.choice(list(r_ings))) for step in random.sample(step_templates, k=random.randint(4, 7))]
        r_id = f"Recipe_{i:03d}"
        recipes.append({
            "id": r_id,
            "ings": set(r_ings),
            "steps": r_steps,
            "step_count": len(r_steps),
            "core_ings": set(base_ings)
        })

        # 菜单：至少包含主材，80%概率添加一个非核心食材
        m_ings = base_ings.copy()
        if len(r_ings) > len(base_ings) and random.random() < 0.8:
            extra = random.choice([ing for ing in r_ings if ing not in base_ings])
            m_ings.append(extra)
        m_id = f"Menu_{i:03d}"
        menus.append({
            "id": m_id,
            "name": f"招牌菜_{i:03d}",
            "price": random.randint(28, 198),
            "ings": set(m_ings),
            "core_ings": set(base_ings)
        })

        secret_mapping[r_id] = m_id

    # 打乱顺序，隐藏直接对应关系
    random.shuffle(recipes)
    random.shuffle(menus)
    return recipes, menus, secret_mapping