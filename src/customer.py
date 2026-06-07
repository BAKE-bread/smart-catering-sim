# customer.py
"""
顾客线程：知道自己想点的菜谱（ID），通过与厨师组交互，逐步缩小候选菜单范围，
最终确认订单。支持探针（直接询问菜谱ID）加速。
"""

import queue
import random
import time
from typing import List, Dict, Set

from config import ENABLE_CHEF_LEARNING, ENABLE_SHARED_KNOWLEDGE
from utils import safe_print
import global_state as gs

def customer_worker(
    customer_id: int,
    target_recipe: Dict,
    all_recipes: List[Dict],
    chef_group_queues: Dict[frozenset[int], queue.Queue],
    customer_response_queue: queue.Queue,
    assigned_chef_group: Set[int],
    true_mapping: Dict[str, str]
) -> None:
    """
    顾客线程主逻辑。
    """
    cust_name = f"顾客_{customer_id:02d}"
    chef_group_str = f"厨师组[{', '.join(map(str, assigned_chef_group))}]"
    candidates = all_recipes.copy()          # 候选菜谱列表（顾客视角）
    used_features = set()
    round_num = 0
    serving_chefs = set()
    target_true_menu_id = true_mapping[target_recipe['id']]
    target_recipe_id = target_recipe['id']

    safe_print(f"[{cust_name}] 进入餐厅，被分配给{chef_group_str}，开始浏览菜谱...", "purple")
    safe_print(f"[{cust_name}] 心中目标: {target_recipe_id} | 真实对应菜单: {target_true_menu_id}\n", "blue")

    # ---------- 探针（第0轮） ----------
    with gs.simulation_data_lock:
        gs.simulation_data["probe_total_count"] += 1

    safe_print(f"--- [{cust_name}] 第 0 轮交互 (探针) ---", "yellow")
    question = f"你们有对应菜谱ID【{target_recipe_id}】的菜吗？"
    group_key = frozenset(assigned_chef_group)
    chef_group_queues[group_key].put((customer_id, "PROBE", target_recipe_id, assigned_chef_group))
    safe_print(f"[{cust_name}] 向{chef_group_str}提问: {question}", "blue")

    ans, chef_id, menu_id = customer_response_queue.get()
    serving_chefs.add(chef_id)
    chef_name = f"厨师_{chef_id:02d}"

    if ans == "是" and menu_id:
        # 探针命中
        round_num = 1
        safe_print(f"[{cust_name}] 收到{chef_name}的'是'！直接匹配到菜单: {menu_id}", "cyan")
        safe_print(f"[{cust_name}] ✅ 探针匹配成功！跳过所有二分步骤，仅用1轮完成匹配！\n", "cyan")
        with gs.simulation_data_lock:
            gs.simulation_data["customer_rounds"][customer_id] = round_num
            gs.simulation_data["customer_chef_mapping"][customer_id] = list(serving_chefs)
            gs.simulation_data["probe_success_count"] += 1
            gs.simulation_data["learning_effect"].append((True, round_num))
            is_correct = (menu_id == target_true_menu_id)
            gs.simulation_data["match_accuracy"].append((customer_id, is_correct))
            if not is_correct:
                safe_print(f"[{cust_name}] ⚠️ 探针匹配错误！应该是: {target_true_menu_id}", "red")
        return  # 顾客完成点餐，直接结束

    # ---------- 探针失败，回退到二分查找 ----------
    safe_print(f"[{cust_name}] 收到{chef_name}的'否'。探针未命中，开始基于食材的二分查找...\n", "yellow")

    while len(candidates) > 1:
        round_num += 1

        # 优先选择未用过的主材特征，其次辅料
        core_ing_counts = {}
        for r in candidates:
            for ing in r['core_ings']:
                if ing not in used_features:
                    core_ing_counts[ing] = core_ing_counts.get(ing, 0) + 1

        best_ing = None
        min_diff = float('inf')
        target_size = len(candidates) / 2

        for ing, count in core_ing_counts.items():
            diff = abs(count - target_size)
            if diff < min_diff and 0 < count < len(candidates):
                min_diff = diff
                best_ing = ing

        if not best_ing:
            # 所有主材已用，使用辅料
            safe_print(f"[{cust_name}] ⚠️ 所有主材特征已使用完毕，开始使用辅料特征...", "yellow")
            noise_counts = {}
            for r in candidates:
                for ing in r['ings']:
                    if ing not in used_features and ing.startswith("[辅料]"):
                        noise_counts[ing] = noise_counts.get(ing, 0) + 1
            for ing, count in noise_counts.items():
                diff = abs(count - target_size)
                if diff < min_diff and 0 < count < len(candidates):
                    min_diff = diff
                    best_ing = ing
            if not best_ing:
                safe_print(f"[{cust_name}] ❌ 所有特征均已使用，无法继续区分！", "red")
                break

        used_features.add(best_ing)
        # has_ing = best_ing in target_recipe['ings']

        # --- 以下噪声逻辑 ---
        from config import ENABLE_CUSTOMER_NOISE, CUSTOMER_NOISE_PROB
        
        has_ing = best_ing in target_recipe['ings']
        
        # 模拟顾客记忆噪音：随机反转“包含/不包含”
        if ENABLE_CUSTOMER_NOISE and random.random() < CUSTOMER_NOISE_PROB:
            has_ing = not has_ing
            safe_print(f"[{cust_name}] ⚠️ 发生记忆错乱！把主要食材特征记反了。", "red")
        # ----------------------

        safe_print(f"--- [{cust_name}] 第 {round_num} 轮交互 ---", "yellow")

        safe_print(f"--- [{cust_name}] 第 {round_num} 轮交互 ---", "yellow")
        question = f"我想要的菜，主要食材{'包含' if has_ing else '不包含'}【{best_ing}】，你们菜单上有符合的吗？"
        chef_group_queues[group_key].put((customer_id, "INCLUDE" if has_ing else "EXCLUDE", best_ing, assigned_chef_group))
        safe_print(f"[{cust_name}] 向{chef_group_str}提问: {question}", "blue")

        ans, chef_id = customer_response_queue.get()
        serving_chefs.add(chef_id)
        chef_name = f"厨师_{chef_id:02d}"

        if ans == "是":
            if has_ing:
                new_candidates = [r for r in candidates if best_ing in r['ings']]
            else:
                new_candidates = [r for r in candidates if best_ing not in r['ings']]
            if new_candidates:
                candidates = new_candidates
                safe_print(f"[{cust_name}] 收到{chef_name}的'是'。本地候选菜谱降至: {len(candidates)}", "blue")
            else:
                safe_print(f"[{cust_name}] ⚠️ 收到'是'但无匹配菜谱，保留原候选集。", "yellow")
        else:
            safe_print(f"[{cust_name}] ❌ 收到{chef_name}的'否'！特征【{best_ing}】在菜单端不存在，无法用于区分。", "red")

    # ---------- 确认订单 ----------
    final_recipe = candidates[0] if candidates else None
    if final_recipe:
        safe_print(f"\n[{cust_name}] 我确定了，就是这道菜：{final_recipe['id']}！", "blue")
        chef_group_queues[group_key].put((customer_id, "CONFIRM", final_recipe['id'], assigned_chef_group))
        final_menu_id, chef_id = customer_response_queue.get()
        serving_chefs.add(chef_id)
        if final_menu_id:
            safe_print(f"[{cust_name}] 收到厨师_{chef_id:02d}确认，订单已提交！匹配到菜单: {final_menu_id}\n", "purple")
            with gs.simulation_data_lock:
                gs.simulation_data["customer_rounds"][customer_id] = round_num
                gs.simulation_data["customer_chef_mapping"][customer_id] = list(serving_chefs)
                gs.simulation_data["learning_effect"].append((False, round_num))
                is_correct = (final_menu_id == target_true_menu_id)
                gs.simulation_data["match_accuracy"].append((customer_id, is_correct))
                if not is_correct:
                    safe_print(f"[{cust_name}] ⚠️  匹配错误！应该是: {target_true_menu_id}", "red")
        else:
            safe_print(f"[{cust_name}] ❌ 无法确认订单，点餐失败！\n", "red")
    else:
        chef_group_queues[group_key].put((customer_id, "CANCEL", None, assigned_chef_group))
        safe_print(f"[{cust_name}] ❌ 无法确定菜品，取消订单。\n", "red")