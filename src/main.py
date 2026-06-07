# main.py
"""
仿真主程序：初始化数据、启动厨师和顾客线程、收集结果、生成图表。
"""

import math
import random
import time
import queue
import threading

from config import (
    N, CUSTOMER_ARRIVAL_MIN_DELAY, CUSTOMER_ARRIVAL_MAX_DELAY,
    CHEF_CUSTOMER_GROUPS, ENABLE_CHEF_LEARNING, ENABLE_SHARED_KNOWLEDGE,
    LOCAL_KNOWLEDGE_CAPACITY, KNOWLEDGE_SYNC_DELAY_PROB
)
from data_generator import generate_data
from customer import customer_worker
from chef import chef_worker
from visualization import generate_analysis_charts
from utils import safe_print
import global_state as gs

def main():
    print(f"==== 多厨师组多顾客随机进入匹配协议仿真 (N={N}) ====")
    print(f"厨师学习功能: {'开启' if ENABLE_CHEF_LEARNING else '关闭'}")
    print(f"全局共享知识库: {'开启' if ENABLE_SHARED_KNOWLEDGE else '关闭'}")
    print(f"本地知识库容量: {LOCAL_KNOWLEDGE_CAPACITY} 个匹配关系")
    print(f"知识库同步延迟概率: {KNOWLEDGE_SYNC_DELAY_PROB*100:.1f}%")
    print(f"顾客到达间隔: {CUSTOMER_ARRIVAL_MIN_DELAY}-{CUSTOMER_ARRIVAL_MAX_DELAY}秒\n")

    # 1. 生成全局数据
    recipes, menus, true_mapping = generate_data(N)
    theoretical_worst_rounds = math.ceil(math.log2(N))
    theoretical_avg_rounds = math.log2(N)
    print(f"[系统] 生成了 {N} 道菜品的菜谱和菜单")
    print(f"[系统] 理论最坏情况匹配轮次: {theoretical_worst_rounds}")
    print(f"[系统] 理论平均情况匹配轮次: {theoretical_avg_rounds:.2f}\n")

    # 2. 解析拓扑关系
    all_customer_ids = set()
    all_chef_ids = set()
    for chef_group, customer_group in CHEF_CUSTOMER_GROUPS:
        all_customer_ids.update(customer_group)
        all_chef_ids.update(chef_group)
    all_customer_ids = sorted(all_customer_ids)
    all_chef_ids = sorted(all_chef_ids)
    num_customers = len(all_customer_ids)
    num_chefs = len(all_chef_ids)

    print(f"[系统] 配置: {num_chefs} 位厨师组成 {len(CHEF_CUSTOMER_GROUPS)} 个厨师组")
    print(f"[系统] 服务 {num_customers} 位顾客")
    for i, (chef_group, cust_group) in enumerate(CHEF_CUSTOMER_GROUPS):
        print(f"[系统] 厨师组{i+1}: 厨师{chef_group} 负责顾客{cust_group}")
    print()

    # 3. 重置全局状态
    gs.global_customer_states.clear()
    gs.shared_knowledge.clear()
    for key in gs.simulation_data:
        if isinstance(gs.simulation_data[key], dict):
            gs.simulation_data[key].clear()
        elif isinstance(gs.simulation_data[key], list):
            gs.simulation_data[key].clear()
    gs.simulation_data["probe_success_count"] = 0
    gs.simulation_data["probe_total_count"] = 0

    # 4. 创建队列
    chef_group_queues: Dict[frozenset[int], queue.Queue] = {}
    for chef_group, _ in CHEF_CUSTOMER_GROUPS:
        group_key = frozenset(chef_group)
        chef_group_queues[group_key] = queue.Queue()

    customer_response_queues = {cid: queue.Queue() for cid in all_customer_ids}

    # 5. 启动厨师线程
    chef_threads = {}
    for chef_id in all_chef_ids:
        t = threading.Thread(
            target=chef_worker,
            args=(chef_id, menus, chef_group_queues, customer_response_queues, true_mapping)
        )
        chef_threads[chef_id] = t
        t.start()

    # 6. 启动顾客线程（随机到达时间）
    customer_threads = {}
    for customer_id in all_customer_ids:
        arrival_delay = random.uniform(CUSTOMER_ARRIVAL_MIN_DELAY, CUSTOMER_ARRIVAL_MAX_DELAY)
        time.sleep(arrival_delay)

        # 找出该顾客所属厨师组
        assigned_chef_groups = set()
        for chef_group, customer_group in CHEF_CUSTOMER_GROUPS:
            if customer_id in customer_group:
                assigned_chef_groups.update(chef_group)

        target_recipe = random.choice(recipes)
        t = threading.Thread(
            target=customer_worker,
            args=(customer_id, target_recipe, recipes,
                  chef_group_queues, customer_response_queues[customer_id],
                  assigned_chef_groups, true_mapping)
        )
        customer_threads[customer_id] = t
        t.start()

    # 7. 等待所有顾客完成
    for t in customer_threads.values():
        t.join()

    # 8. 通知所有厨师退出
    for q in chef_group_queues.values():
        q.put(None)
    for t in chef_threads.values():
        t.join()

    # 9. 打印统计结果
    print("\n" + "="*80)
    print("📊 仿真结果统计")
    print("="*80)
    print(f"总菜品数: {N}")
    print(f"厨师数量: {num_chefs}")
    print(f"厨师组数量: {len(CHEF_CUSTOMER_GROUPS)}")
    print(f"顾客数量: {num_customers}")
    print(f"理论最坏情况匹配轮次: {theoretical_worst_rounds}")
    print(f"理论平均情况匹配轮次: {theoretical_avg_rounds:.2f}")

    if gs.simulation_data["customer_rounds"]:
        rounds_vals = list(gs.simulation_data["customer_rounds"].values())
        avg_rounds = np.mean(rounds_vals)
        max_rounds = max(rounds_vals)
        min_rounds = min(rounds_vals)
        success_rate = len(gs.simulation_data["customer_rounds"]) / num_customers * 100
        print(f"\n顾客匹配轮次统计:")
        print(f"  成功点餐率: {success_rate:.1f}%")
        print(f"  平均轮次: {avg_rounds:.2f}")
        print(f"  最快轮次: {min_rounds}")
        print(f"  最慢轮次: {max_rounds}")
        print(f"  与理论最坏值偏差: {(avg_rounds - theoretical_worst_rounds)/theoretical_worst_rounds*100:.1f}%")

    if gs.simulation_data["probe_total_count"] > 0:
        probe_success = gs.simulation_data["probe_success_count"]
        probe_total = gs.simulation_data["probe_total_count"]
        print(f"\n探针匹配统计:")
        print(f"  探针总次数: {probe_total}")
        print(f"  探针成功次数: {probe_success}")
        print(f"  探针成功率: {probe_success/probe_total*100:.1f}%")

    print(f"\n厨师负载统计:")
    for chef_id in all_chef_ids:
        total_rounds = gs.simulation_data["chef_total_rounds"].get(chef_id, 0)
        customers_served = gs.simulation_data["chef_customers_served"].get(chef_id, 0)
        avg_per_customer = total_rounds / customers_served if customers_served else 0
        print(f"  厨师_{chef_id:02d}: 服务{customers_served}位顾客，总处理{total_rounds}轮，平均{avg_per_customer:.2f}轮/顾客")

    if ENABLE_CHEF_LEARNING:
        total_learned = sum([data[-1][1] for data in gs.simulation_data["chef_knowledge_growth"].values()])
        print(f"\n学习效果统计:")
        print(f"  所有厨师本地知识库总大小: {total_learned} 个匹配关系")
        if ENABLE_SHARED_KNOWLEDGE:
            print(f"  全局共享知识库大小: {len(gs.shared_knowledge)} 个匹配关系")
        if gs.simulation_data["learning_effect"]:
            learned_rounds = [r for (is_learned, r) in gs.simulation_data["learning_effect"] if is_learned]
            not_learned_rounds = [r for (is_learned, r) in gs.simulation_data["learning_effect"] if not is_learned]
            if learned_rounds and not_learned_rounds:
                avg_learned = np.mean(learned_rounds)
                avg_not = np.mean(not_learned_rounds)
                improvement = (avg_not - avg_learned) / avg_not * 100
                print(f"  探针匹配平均轮次: {avg_learned:.2f}")
                print(f"  二分匹配平均轮次: {avg_not:.2f}")
                print(f"  学习提升效率: {improvement:.1f}%")

    if gs.simulation_data["match_accuracy"]:
        correct = sum(1 for _, ok in gs.simulation_data["match_accuracy"] if ok)
        total = len(gs.simulation_data["match_accuracy"])
        print(f"\n匹配准确率统计:")
        print(f"  正确匹配数: {correct}")
        print(f"  错误匹配数: {total - correct}")
        print(f"  整体准确率: {correct/total*100:.1f}%")

    # 10. 生成图表
    generate_analysis_charts(theoretical_worst_rounds, theoretical_avg_rounds)

    print("\n✅ 仿真完成！")
    if (len(gs.simulation_data["customer_rounds"]) == num_customers and
        all(ok for _, ok in gs.simulation_data["match_accuracy"])):
        print("所有顾客均成功完成点餐且匹配100%正确")
        if gs.simulation_data["probe_success_count"] > 0:
            rate = gs.simulation_data["probe_success_count"] / gs.simulation_data["probe_total_count"] * 100
            print(f"学习功能的探针成功率达到 {rate:.1f}%")
    else:
        failed = num_customers - len(gs.simulation_data["customer_rounds"])
        wrong = sum(1 for _, ok in gs.simulation_data["match_accuracy"] if not ok)
        print(f"⚠️  有 {failed} 位顾客点餐失败，{wrong} 位顾客匹配错误。")

if __name__ == "__main__":
    import numpy as np   # 用于main中的平均计算
    main()