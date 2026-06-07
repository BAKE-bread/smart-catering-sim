# chef.py
"""
厨师线程：掌握菜单列表，回答顾客的提问（是/否），从成功匹配中学习菜谱→菜单映射。
"""

import queue
import time
import random
from typing import Dict, List, Set

from config import (
    ENABLE_CHEF_LEARNING, ENABLE_SHARED_KNOWLEDGE,
    LOCAL_KNOWLEDGE_CAPACITY, KNOWLEDGE_SYNC_DELAY_PROB
)
from utils import safe_print
from lru_cache import LRUCache
import global_state as gs

def chef_worker(
    chef_id: int,
    all_menus: List[Dict],
    chef_group_queues: Dict[frozenset[int], queue.Queue],
    customer_response_queues: Dict[int, queue.Queue],
    true_mapping: Dict[str, str]   # 实际上厨师不知道此映射，仅供调试
) -> None:
    """
    厨师线程主逻辑。
    """
    chef_name = f"厨师_{chef_id:02d}"
    local_knowledge = LRUCache(LOCAL_KNOWLEDGE_CAPACITY)
    start_time = time.time()

    # 建立特征索引（主材 -> 菜单ID集合）
    feature_index: Dict[str, Set[str]] = {}
    for menu in all_menus:
        for ing in menu['core_ings']:
            feature_index.setdefault(ing, set()).add(menu['id'])

    safe_print(f"[{chef_name}] 准备就绪，等待顾客点餐...\n", "green")

    # 记录知识增长起点
    with gs.simulation_data_lock:
        gs.simulation_data["chef_knowledge_growth"][chef_id] = [(0, 0)]
        gs.simulation_data["chef_total_rounds"][chef_id] = 0
        gs.simulation_data["chef_customers_served"][chef_id] = 0

    # 找出所有包含本厨师的队列
    my_groups = [group for group in chef_group_queues.keys() if chef_id in group]

    while True:
        item = None
        active_group = None
        for group in my_groups:
            try:
                item = chef_group_queues[group].get_nowait()
                active_group = group
                break
            except queue.Empty:
                continue

        if active_group is None:
            time.sleep(0.001)
            continue

        if item is None:
            # 收到毒药丸，传递给同组其他厨师后退出
            chef_group_queues[active_group].put(None)
            break

        customer_id, cmd, data, allowed_chefs = item
        cust_name = f"顾客_{customer_id:02d}"

        # 获取或创建顾客状态
        with gs.customer_state_lock:
            if customer_id not in gs.global_customer_states:
                gs.global_customer_states[customer_id] = gs.CustomerState(
                    candidates=all_menus.copy()
                )
                safe_print(f"[{chef_name}] 开始接待顾客: {cust_name}", "green")
            state = gs.global_customer_states[customer_id]

            if state.confirmed or state.canceled:
                chef_group_queues[active_group].task_done()
                continue

        # ---------- 处理不同命令 ----------
        if cmd == "PROBE":
            recipe_id = data
            with gs.customer_state_lock:
                state.rounds += 1
                # 先查本地知识库
                menu_id = local_knowledge.get(recipe_id)
                # 再查全局知识库（带模拟延迟）
                if not menu_id and ENABLE_SHARED_KNOWLEDGE:
                    with gs.shared_knowledge_lock:
                        if recipe_id in gs.shared_knowledge:
                            if random.random() > KNOWLEDGE_SYNC_DELAY_PROB:
                                menu_id, _ = gs.shared_knowledge[recipe_id]
                                local_knowledge.put(recipe_id, menu_id)
                                safe_print(f"[{chef_name}] 从全局知识库同步到匹配关系: {recipe_id} <=> {menu_id}", "cyan")
                if menu_id:
                    state.confirmed = True
                    state.final_menu_id = menu_id
                    state.probe_success = True
                    ans = "是"
                    safe_print(f"[{chef_name}] 探针命中！直接返回菜单: {menu_id}", "cyan")
                else:
                    ans = "否"
                    safe_print(f"[{chef_name}] 探针未命中。回应: {ans}", "yellow")
            customer_response_queues[customer_id].put((ans, chef_id, menu_id))

        elif cmd in ("INCLUDE", "EXCLUDE"):
            feature = data
            with gs.customer_state_lock:
                if state.confirmed or state.canceled:
                    chef_group_queues[active_group].task_done()
                    continue
                state.rounds += 1
                # 利用特征索引快速判断
                if feature in feature_index:
                    feature_menu_ids = feature_index[feature]
                    if cmd == "INCLUDE":
                        temp = [m for m in state.candidates if m['id'] in feature_menu_ids]
                    else:
                        temp = [m for m in state.candidates if m['id'] not in feature_menu_ids]
                else:
                    # 特征不在索引中（可能是辅料或主材未出现在任何菜单中）
                    if cmd == "INCLUDE":
                        temp = [m for m in state.candidates if feature in m['ings']]
                    else:
                        temp = [m for m in state.candidates if feature not in m['ings']]
                # if len(temp) > 0:
                #     ans = "是"
                #     state.candidates = temp
                #     safe_print(f"[{chef_name}] 验证成功，有符合的菜单。回应: {ans}", "green")
                # else:
                #     ans = "否"
                #     safe_print(f"[{chef_name}] ⚠️ 验证失败，无任何匹配菜单！拒绝更新候选集。回应: {ans}", "red")
                
                # --- 以下噪音注入与防崩溃处理逻辑 ---
                from config import ENABLE_CHEF_NOISE, CHEF_NOISE_PROB
                
                is_true_match = len(temp) > 0
                ans_bool = is_true_match
                
                # 模拟厨师同步延迟/误答噪音
                if ENABLE_CHEF_NOISE and random.random() < CHEF_NOISE_PROB:
                    ans_bool = not ans_bool
                    safe_print(f"[{chef_name}] ⚠️ 发生信息更新延迟！实际应为{'是' if is_true_match else '否'}，却误答为了{'是' if ans_bool else '否'}", "red")

                if ans_bool:
                    ans = "是"
                    # 【防崩溃关键】仅在真正有菜单时才更新候选集，若因噪音误答为"是"但实际为空，保持原候选集不动
                    if is_true_match:
                        state.candidates = temp
                    safe_print(f"[{chef_name}] 回答: {ans} (顾客候选范围缩小)", "green")
                else:
                    ans = "否"
                    safe_print(f"[{chef_name}] ⚠️ 验证失败或误答为否，拒绝更新候选集。回答: {ans}", "red")
                # ----------------------

                safe_print(f"[{chef_name}] {cust_name} 剩余候选菜单数: {len(state.candidates)}", "green")
            customer_response_queues[customer_id].put((ans, chef_id))

        elif cmd == "CONFIRM":
            recipe_id = data
            with gs.customer_state_lock:
                if state.confirmed or state.canceled:
                    chef_group_queues[active_group].task_done()
                    continue
                if len(state.candidates) == 1:
                    final_menu = state.candidates[0]
                    state.confirmed = True
                    state.final_menu_id = final_menu['id']
                    safe_print(f"\n[{chef_name}] {cust_name} 订单确认！最终锁定菜单: {final_menu['id']} ({final_menu['name']}, ¥{final_menu['price']})", "green")
                    # 学习映射
                    if ENABLE_CHEF_LEARNING:
                        local_knowledge.put(recipe_id, final_menu['id'])
                        safe_print(f"[{chef_name}] 学习到新的匹配关系: {recipe_id} <=> {final_menu['id']}", "cyan")
                        if ENABLE_SHARED_KNOWLEDGE:
                            with gs.shared_knowledge_lock:
                                if recipe_id not in gs.shared_knowledge:
                                    gs.shared_knowledge[recipe_id] = (final_menu['id'], time.time())
                                    safe_print(f"[{chef_name}] 同步匹配关系到全局知识库", "cyan")
                        # 记录知识增长曲线
                        current_time = time.time() - start_time
                        with gs.simulation_data_lock:
                            gs.simulation_data["chef_knowledge_growth"][chef_id].append((current_time, len(local_knowledge)))
                    customer_response_queues[customer_id].put((final_menu['id'], chef_id))
                else:
                    safe_print(f"\n[{chef_name}] ❌ {cust_name} 候选菜单不唯一({len(state.candidates)}个)，无法确认订单！", "red")
                    customer_response_queues[customer_id].put((None, chef_id))
            with gs.simulation_data_lock:
                gs.simulation_data["chef_customers_served"][chef_id] += 1

        elif cmd == "CANCEL":
            with gs.customer_state_lock:
                state.canceled = True
                safe_print(f"\n[{chef_name}] {cust_name} 取消订单。", "yellow")

        # 更新轮次计数
        with gs.simulation_data_lock:
            gs.simulation_data["chef_total_rounds"][chef_id] += 1

        chef_group_queues[active_group].task_done()

    safe_print(f"\n[{chef_name}] 所有顾客已接待完毕，下班！", "green")
    safe_print(f"[{chef_name}] 本次共服务 {gs.simulation_data['chef_customers_served'][chef_id]} 位顾客，本地学到 {len(local_knowledge)} 个匹配关系", "cyan")