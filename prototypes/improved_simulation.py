import threading
import queue
import random
import time
import math
from typing import Dict, List, Optional, Tuple, Set
import matplotlib.pyplot as plt
import numpy as np
from dataclasses import dataclass
from collections import OrderedDict

# ================= 系统核心参数 =================
N = 500  # 总菜品数量
SIMULATION_SPEED = 0.001  # 打印延迟(秒)
ENABLE_CHEF_LEARNING = True  # 开启厨师学习功能
ENABLE_SHARED_KNOWLEDGE = True  # 厨师之间共享知识库
CUSTOMER_ARRIVAL_MIN_DELAY = 0.05  # 顾客到达最小间隔(秒)
CUSTOMER_ARRIVAL_MAX_DELAY = 0.5  # 顾客到达最大间隔(秒)
LOCAL_KNOWLEDGE_CAPACITY = 100  # 厨师本地知识库最大容量(LRU)
KNOWLEDGE_SYNC_DELAY_PROB = 0.1  # 全局知识库同步延迟概率
KNOWLEDGE_SYNC_DELAY_MAX = 0.5  # 最大同步延迟(秒)

# 全局线程安全资源
print_lock = threading.Lock()
simulation_data_lock = threading.Lock()
shared_knowledge_lock = threading.Lock()
customer_state_lock = threading.Lock()

# 全局共享知识库 (菜谱ID -> (菜单ID, 最后访问时间))
shared_knowledge: Dict[str, Tuple[str, float]] = {}

# 全局共享顾客状态
@dataclass
class CustomerState:
    candidates: List[Dict]
    rounds: int = 0
    confirmed: bool = False
    canceled: bool = False
    final_menu_id: Optional[str] = None
    probe_success: bool = False  # 是否通过探针直接匹配成功

global_customer_states: Dict[int, CustomerState] = {}

# 全局仿真数据收集
simulation_data = {
    "customer_rounds": {},  # {customer_id: rounds}
    "chef_total_rounds": {},  # {chef_id: total_rounds}
    "chef_customers_served": {},  # {chef_id: customers_served}
    "chef_knowledge_growth": {},  # {chef_id: [(time, knowledge_size)]}
    "learning_effect": [],  # [(is_learned, rounds)]
    "customer_chef_mapping": {},  # {customer_id: [chef_ids_that_served_them]}
    "match_accuracy": [],  # [(customer_id, is_correct)]
    "probe_success_count": 0,  # 探针成功次数
    "probe_total_count": 0  # 探针总次数
}

# LRU缓存实现
class LRUCache:
    def __init__(self, capacity: int):
        self.cache = OrderedDict()
        self.capacity = capacity
    
    def get(self, key: str) -> Optional[str]:
        if key not in self.cache:
            return None
        self.cache.move_to_end(key)
        return self.cache[key]
    
    def put(self, key: str, value: str) -> None:
        if key in self.cache:
            self.cache.move_to_end(key)
        self.cache[key] = value
        if len(self.cache) > self.capacity:
            self.cache.popitem(last=False)
    
    def __len__(self) -> int:
        return len(self.cache)
    
    def items(self):
        return self.cache.items()

def safe_print(msg: str, color: str = "") -> None:
    """带锁的安全打印，防止多线程输出错乱"""
    colors = {
        "blue": "\033[94m", "green": "\033[92m", 
        "yellow": "\033[93m", "red": "\033[91m", 
        "purple": "\033[95m", "cyan": "\033[96m", "end": "\033[0m"
    }
    c_start = colors.get(color, "")
    c_end = colors["end"] if color else ""
    with print_lock:
        print(f"{c_start}{msg}{c_end}")
        time.sleep(SIMULATION_SPEED)

# ================= 智能数据生成 =================
def generate_data(n: int) -> Tuple[List[Dict], List[Dict], Dict[str, str]]:
    """
    智能生成符合约束的菜谱、菜单和秘密映射
    保证：1. 每个菜谱有唯一的主材组合 2. 菜单食材是菜谱食材的子集
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
        
        r_ings = base_ings + random.sample(noise_ings, k=random.randint(4, 8))
        r_steps = [step.format(random.choice(list(r_ings))) for step in random.sample(step_templates, k=random.randint(4, 7))]
        r_id = f"Recipe_{i:03d}"
        recipes.append({
            "id": r_id,
            "ings": set(r_ings),
            "steps": r_steps,
            "step_count": len(r_steps),
            "core_ings": set(base_ings)  # 明确标记主材集合
        })
        
        m_ings = base_ings.copy()
        if len(r_ings) > len(base_ings) and random.random() < 0.8:
            m_ings.append(random.choice([ing for ing in r_ings if ing not in base_ings]))
        m_id = f"Menu_{i:03d}"
        menus.append({
            "id": m_id,
            "name": f"招牌菜_{i:03d}",
            "price": random.randint(28, 198),
            "ings": set(m_ings),
            "core_ings": set(base_ings)  # 明确标记主材集合
        })
        
        secret_mapping[r_id] = m_id
    
    random.shuffle(recipes)
    random.shuffle(menus)
    
    return recipes, menus, secret_mapping

# ================= 智能化顾客线程 =================
def customer_worker(
    customer_id: int,
    target_recipe: Dict,
    all_recipes: List[Dict],
    chef_group_queues: Dict[frozenset[int], queue.Queue],
    customer_response_queue: queue.Queue,
    assigned_chef_group: Set[int],
    true_mapping: Dict[str, str]
) -> None:
    """顾客线程：仅能阅读菜谱，通过提问与指定厨师组交互"""
    customer_name = f"顾客_{customer_id:02d}"
    chef_group_str = f"厨师组[{', '.join(map(str, assigned_chef_group))}]"
    candidates = all_recipes.copy()
    used_features = set()
    round_num = 0
    serving_chefs = set()
    target_true_menu_id = true_mapping[target_recipe['id']]
    target_recipe_id = target_recipe['id']
    
    safe_print(f"[{customer_name}] 进入餐厅，被分配给{chef_group_str}，开始浏览菜谱...", "purple")
    safe_print(f"[{customer_name}] 心中目标: {target_recipe_id} | 真实对应菜单: {target_true_menu_id}\n", "blue")
    
    # O(1)哈希探针短路匹配
    probe_success = False
    with simulation_data_lock:
        simulation_data["probe_total_count"] += 1
    
    safe_print(f"--- [{customer_name}] 第 0 轮交互 (探针) ---", "yellow")
    question = f"你们有对应菜谱ID【{target_recipe_id}】的菜吗？"
    
    # 直接路由到对应厨师组队列
    group_key = frozenset(assigned_chef_group)
    chef_group_queues[group_key].put((customer_id, "PROBE", target_recipe_id, assigned_chef_group))
    safe_print(f"[{customer_name}] 向{chef_group_str}提问: {question}", "blue")
    
    # 等待厨师回应
    ans, chef_id, menu_id = customer_response_queue.get()
    serving_chefs.add(chef_id)
    chef_name = f"厨师_{chef_id:02d}"
    
    if ans == "是" and menu_id:
        probe_success = True
        round_num = 1
        safe_print(f"[{customer_name}] 收到{chef_name}的'是'！直接匹配到菜单: {menu_id}", "cyan")
        safe_print(f"[{customer_name}] 🚀 探针匹配成功！跳过所有二分步骤，仅用1轮完成匹配！\n", "cyan")
        
        # 记录仿真数据
        with simulation_data_lock:
            simulation_data["customer_rounds"][customer_id] = round_num
            simulation_data["customer_chef_mapping"][customer_id] = list(serving_chefs)
            simulation_data["probe_success_count"] += 1
            simulation_data["learning_effect"].append((True, round_num))
            # 验证匹配正确性
            is_correct = (menu_id == target_true_menu_id)
            simulation_data["match_accuracy"].append((customer_id, is_correct))
            if not is_correct:
                safe_print(f"[{customer_name}] ⚠️  探针匹配错误！应该是: {target_true_menu_id}", "red")
    else:
        safe_print(f"[{customer_name}] 收到{chef_name}的'否'。探针未命中，开始基于食材的二分查找...\n", "yellow")
        
        # 探针未命中，回退到传统二分协议
        while len(candidates) > 1:
            round_num += 1
            
            # 严格优先使用主材特征
            core_ing_counts = {}
            for r in candidates:
                for ing in r['core_ings']:
                    if ing not in used_features:
                        core_ing_counts[ing] = core_ing_counts.get(ing, 0) + 1
            
            # 寻找最能平分当前候选集的主材特征
            best_ing: Optional[str] = None
            min_diff = float('inf')
            target_size = len(candidates) / 2
            
            for ing, count in core_ing_counts.items():
                diff = abs(count - target_size)
                if diff < min_diff and 0 < count < len(candidates):
                    min_diff = diff
                    best_ing = ing
            
            # 只有当所有主材都使用完毕后，才使用辅料特征
            if not best_ing:
                safe_print(f"[{customer_name}] ⚠️ 所有主材特征已使用完毕，开始使用辅料特征...", "yellow")
                noise_ing_counts = {}
                for r in candidates:
                    for ing in r['ings']:
                        if ing not in used_features and ing.startswith("[辅料]"):
                            noise_ing_counts[ing] = noise_ing_counts.get(ing, 0) + 1
                
                for ing, count in noise_ing_counts.items():
                    diff = abs(count - target_size)
                    if diff < min_diff and 0 < count < len(candidates):
                        min_diff = diff
                        best_ing = ing
                
                if not best_ing:
                    safe_print(f"[{customer_name}] ❌ 所有特征均已使用，无法继续区分！", "red")
                    break
            
            used_features.add(best_ing)
            has_ing = best_ing in target_recipe['ings']
            
            safe_print(f"--- [{customer_name}] 第 {round_num} 轮交互 ---", "yellow")
            question = f"我想要的菜，主要食材{'包含' if has_ing else '不包含'}【{best_ing}】，你们菜单上有符合的吗？"
            
            # 直接路由到对应厨师组队列
            chef_group_queues[group_key].put((customer_id, "INCLUDE" if has_ing else "EXCLUDE", best_ing, assigned_chef_group))
            safe_print(f"[{customer_name}] 向{chef_group_str}提问: {question}", "blue")
            
            # 等待厨师回应
            ans, chef_id = customer_response_queue.get()
            serving_chefs.add(chef_id)
            chef_name = f"厨师_{chef_id:02d}"
            
            # 根据厨师回答正确更新本地候选集
            if ans == "是":
                if has_ing:
                    new_candidates = [r for r in candidates if best_ing in r['ings']]
                else:
                    new_candidates = [r for r in candidates if best_ing not in r['ings']]
                
                # 只有当新候选集非空时才更新
                if new_candidates:
                    candidates = new_candidates
                    safe_print(f"[{customer_name}] 收到{chef_name}的'是'。本地候选菜谱降至: {len(candidates)}", "blue")
                else:
                    safe_print(f"[{customer_name}] ⚠️ 收到'是'但无匹配菜谱，保留原候选集。", "yellow")
            else:
                # 收到"否"说明该特征在菜单端不存在，无法用于区分，不更新候选集
                safe_print(f"[{customer_name}] ❌ 收到{chef_name}的'否'！特征【{best_ing}】在菜单端不存在，无法用于区分。", "red")
        
        # 匹配完成，发送确认请求
        final_recipe = candidates[0] if candidates else None
        if final_recipe:
            safe_print(f"\n[{customer_name}] 我确定了，就是这道菜：{final_recipe['id']}！", "blue")
            chef_group_queues[group_key].put((customer_id, "CONFIRM", final_recipe['id'], assigned_chef_group))
            final_menu_id, chef_id = customer_response_queue.get()
            serving_chefs.add(chef_id)
            
            if final_menu_id:
                safe_print(f"[{customer_name}] 收到厨师_{chef_id:02d}确认，订单已提交！匹配到菜单: {final_menu_id}\n", "purple")
                # 记录仿真数据
                with simulation_data_lock:
                    simulation_data["customer_rounds"][customer_id] = round_num
                    simulation_data["customer_chef_mapping"][customer_id] = list(serving_chefs)
                    simulation_data["learning_effect"].append((False, round_num))
                    # 验证匹配正确性
                    is_correct = (final_menu_id == target_true_menu_id)
                    simulation_data["match_accuracy"].append((customer_id, is_correct))
                    if not is_correct:
                        safe_print(f"[{customer_name}] ⚠️  匹配错误！应该是: {target_true_menu_id}", "red")

            else:
                safe_print(f"[{customer_name}] ❌ 无法确认订单，点餐失败！\n", "red")
        else:
            chef_group_queues[group_key].put((customer_id, "CANCEL", None, assigned_chef_group))
            safe_print(f"[{customer_name}] ❌ 无法确定菜品，取消订单。\n", "red")

# ================= 智能化厨师线程 =================
def chef_worker(
    chef_id: int,
    all_menus: List[Dict],
    chef_group_queues: Dict[frozenset[int], queue.Queue],
    customer_response_queues: Dict[int, queue.Queue],
    true_mapping: Dict[str, str]
) -> None:
    """厨师线程：仅能阅读菜单，仅能回应是/否，可加入任意厨师组"""
    chef_name = f"厨师_{chef_id:02d}"
    # LRU本地知识库
    local_knowledge = LRUCache(LOCAL_KNOWLEDGE_CAPACITY)
    start_time = time.time()
    
    # 特征索引缓存：建立主材特征到菜单集合的索引，加速本地计算
    feature_index: Dict[str, Set[str]] = {}
    for menu in all_menus:
        for ing in menu['core_ings']:
            if ing not in feature_index:
                feature_index[ing] = set()
            feature_index[ing].add(menu['id'])
    
    safe_print(f"[{chef_name}] 准备就绪，等待顾客点餐...\n", "green")
    
    # 初始化知识增长记录
    with simulation_data_lock:
        simulation_data["chef_knowledge_growth"][chef_id] = [(0, 0)]
        simulation_data["chef_total_rounds"][chef_id] = 0
        simulation_data["chef_customers_served"][chef_id] = 0
    
    # 找出所有包含当前厨师的厨师组
    my_groups = [group for group in chef_group_queues.keys() if chef_id in group]
    
    while True:
        # 轮询所有自己所属的厨师组队列
        item = None
        active_group = None  # 【修复1】：专门记录本次取到任务的具体队列
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
            # 【修复2】：级联毒药丸（击鼓传花）
            # 自己下班前，把 None 放回刚才拿出的队列中，确保同组的下一个人也能收到
            chef_group_queues[active_group].put(None)
            break  # 收到退出信号，安全退出
            
        customer_id, cmd, data, allowed_chefs = item
        
        cust_name = f"顾客_{customer_id:02d}"
        
        # 全局共享顾客状态
        with customer_state_lock:
            if customer_id not in global_customer_states:
                global_customer_states[customer_id] = CustomerState(
                    candidates=all_menus.copy()
                )
                safe_print(f"[{chef_name}] 开始接待顾客: {cust_name}", "green")
            
            state = global_customer_states[customer_id]
            
            if state.confirmed or state.canceled:
                # 【修复1】：只对正确的队列汇报 task_done
                chef_group_queues[active_group].task_done()
                continue
        
        if cmd == "PROBE":
            recipe_id = data
            
            with customer_state_lock:
                if state.confirmed or state.canceled:
                    chef_group_queues[active_group].task_done() # 【修复1】
                    continue
                
                state.rounds += 1
                
                # 先检查本地知识库
                menu_id = local_knowledge.get(recipe_id)
                
                # 如果本地没有，检查全局知识库（模拟同步延迟）
                if not menu_id and ENABLE_SHARED_KNOWLEDGE:
                    with shared_knowledge_lock:
                        if recipe_id in shared_knowledge:
                            if random.random() > KNOWLEDGE_SYNC_DELAY_PROB:
                                menu_id, _ = shared_knowledge[recipe_id]
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
            
            with simulation_data_lock:
                simulation_data["chef_total_rounds"][chef_id] += 1
            
            chef_group_queues[active_group].task_done() # 【修复1】
            
        elif cmd in ("INCLUDE", "EXCLUDE"):
            feature = data
            
            with customer_state_lock:
                if state.confirmed or state.canceled:
                    chef_group_queues[active_group].task_done() # 【修复1】
                    continue
                
                state.rounds += 1
                
                if feature in feature_index:
                    feature_menu_ids = feature_index[feature]
                    if cmd == "INCLUDE":
                        temp_candidates = [m for m in state.candidates if m['id'] in feature_menu_ids]
                    else:
                        temp_candidates = [m for m in state.candidates if m['id'] not in feature_menu_ids]
                else:
                    if cmd == "INCLUDE":
                        temp_candidates = [m for m in state.candidates if feature in m['ings']]
                    else:
                        temp_candidates = [m for m in state.candidates if feature not in m['ings']]
                
                if len(temp_candidates) > 0:
                    ans = "是"
                    state.candidates = temp_candidates
                    safe_print(f"[{chef_name}] 验证成功，有符合的菜单。回应: {ans}", "green")
                else:
                    ans = "否"
                    safe_print(f"[{chef_name}] ⚠️ 验证失败，无任何匹配菜单！拒绝更新候选集。回应: {ans}", "red")
                
                safe_print(f"[{chef_name}] {cust_name} 剩余候选菜单数: {len(state.candidates)}", "green")
            
            customer_response_queues[customer_id].put((ans, chef_id))
            
            with simulation_data_lock:
                simulation_data["chef_total_rounds"][chef_id] += 1
            
            chef_group_queues[active_group].task_done() # 【修复1】
            
        elif cmd == "CONFIRM":
            recipe_id = data
            
            with customer_state_lock:
                if state.confirmed or state.canceled:
                    chef_group_queues[active_group].task_done() # 【修复1】
                    continue
                
                if len(state.candidates) == 1:
                    final_menu = state.candidates[0]
                    state.confirmed = True
                    state.final_menu_id = final_menu['id']
                    safe_print(f"\n[{chef_name}] {cust_name} 订单确认！最终锁定菜单: {final_menu['id']} ({final_menu['name']}, ¥{final_menu['price']})", "green")
                    
                    if ENABLE_CHEF_LEARNING:
                        local_knowledge.put(recipe_id, final_menu['id'])
                        safe_print(f"[{chef_name}] 学习到新的匹配关系: {recipe_id} <=> {final_menu['id']}", "cyan")
                        
                        if ENABLE_SHARED_KNOWLEDGE:
                            with shared_knowledge_lock:
                                if recipe_id not in shared_knowledge:
                                    shared_knowledge[recipe_id] = (final_menu['id'], time.time())
                                    safe_print(f"[{chef_name}] 同步匹配关系到全局知识库", "cyan")
                        
                        current_time = time.time() - start_time
                        with simulation_data_lock:
                            simulation_data["chef_knowledge_growth"][chef_id].append((current_time, len(local_knowledge)))
                    
                    customer_response_queues[customer_id].put((final_menu['id'], chef_id))
                else:
                    safe_print(f"\n[{chef_name}] ❌ {cust_name} 候选菜单不唯一({len(state.candidates)}个)，无法确认订单！", "red")
                    customer_response_queues[customer_id].put((None, chef_id))
                
                with simulation_data_lock:
                    simulation_data["chef_customers_served"][chef_id] += 1
            
            chef_group_queues[active_group].task_done() # 【修复1】
            
        elif cmd == "CANCEL":
            with customer_state_lock:
                state.canceled = True
                safe_print(f"\n[{chef_name}] {cust_name} 取消订单。", "yellow")
            
            chef_group_queues[active_group].task_done() # 【修复1】
    
    safe_print(f"\n[{chef_name}] 所有顾客已接待完毕，下班！", "green")
    safe_print(f"[{chef_name}] 本次共服务 {simulation_data['chef_customers_served'][chef_id]} 位顾客，本地学到 {len(local_knowledge)} 个匹配关系", "cyan")

# ================= 图表生成函数 =================
def generate_analysis_charts(theoretical_worst_rounds: float, theoretical_avg_rounds: float, chef_customer_groups: List[Tuple[Set[int], Set[int]]]):
    """生成多维度分析图表"""
    plt.rcParams['font.sans-serif'] = ['SimHei']  # 解决中文显示问题
    plt.rcParams['axes.unicode_minus'] = False
    
    # 创建4x2的子图布局
    fig, axes = plt.subplots(4, 2, figsize=(16, 24))
    fig.suptitle('多厨师组多顾客匹配协议仿真分析（含真正学习功能）', fontsize=18, fontweight='bold')
    
    # 图1：顾客匹配轮次分布
    ax1 = axes[0, 0]
    if simulation_data["customer_rounds"]:
        customer_ids = list(simulation_data["customer_rounds"].keys())
        rounds = list(simulation_data["customer_rounds"].values())
        
        # 区分探针成功和失败的顾客
        probe_success_rounds = []
        probe_failure_rounds = []
        for cid, r in simulation_data["customer_rounds"].items():
            if global_customer_states[cid].probe_success:
                probe_success_rounds.append(r)
            else:
                probe_failure_rounds.append(r)
        
        ax1.bar(customer_ids, rounds, color='skyblue', alpha=0.7, label='所有顾客')
        if probe_success_rounds:
            ax1.axhline(y=np.mean(probe_success_rounds), color='cyan', linestyle='-', label=f'探针成功平均({np.mean(probe_success_rounds):.1f}轮)')
        if probe_failure_rounds:
            ax1.axhline(y=np.mean(probe_failure_rounds), color='orange', linestyle='-', label=f'探针失败平均({np.mean(probe_failure_rounds):.1f}轮)')
        ax1.axhline(y=theoretical_worst_rounds, color='red', linestyle='--', label=f'理论最坏值({theoretical_worst_rounds}轮)')
        ax1.axhline(y=np.mean(rounds), color='green', linestyle='-', label=f'整体平均({np.mean(rounds):.1f}轮)')
        ax1.set_xlabel('顾客ID')
        ax1.set_ylabel('匹配轮次')
        ax1.set_title('各顾客匹配轮次分布')
        ax1.legend()
        ax1.grid(axis='y', alpha=0.3)
    else:
        ax1.text(0.5, 0.5, '无匹配轮次数据', ha='center', va='center', transform=ax1.transAxes, fontsize=12)
    
    # 图2：探针成功率
    ax2 = axes[0, 1]
    if simulation_data["probe_total_count"] > 0:
        success = simulation_data["probe_success_count"]
        total = simulation_data["probe_total_count"]
        success_rate = success / total * 100
        
        labels = ['探针成功', '探针失败']
        sizes = [success, total - success]
        colors = ['lightgreen', 'salmon']
        
        ax2.pie(sizes, labels=labels, colors=colors, autopct='%1.1f%%', startangle=90)
        ax2.set_title(f'探针匹配成功率: {success_rate:.1f}%')
    else:
        ax2.text(0.5, 0.5, '无探针数据', ha='center', va='center', transform=ax2.transAxes, fontsize=12)
    
    # 图3：厨师负载与效率对比
    ax3 = axes[1, 0]
    chef_ids = list(simulation_data["chef_total_rounds"].keys())
    total_rounds = [simulation_data["chef_total_rounds"][cid] for cid in chef_ids]
    customers_served = [simulation_data["chef_customers_served"][cid] for cid in chef_ids]
    
    x = np.arange(len(chef_ids))
    width = 0.35
    
    ax3.bar(x - width/2, total_rounds, width, label='总处理轮次', color='orange', alpha=0.7)
    ax3.bar(x + width/2, customers_served, width, label='服务顾客数', color='green', alpha=0.7)
    ax3.set_xlabel('厨师ID')
    ax3.set_ylabel('数量')
    ax3.set_title('各厨师负载对比')
    ax3.set_xticks(x)
    ax3.set_xticklabels([f'厨师_{cid:02d}' for cid in chef_ids])
    ax3.legend()
    ax3.grid(axis='y', alpha=0.3)
    
    # 图4：学习效果对比
    ax4 = axes[1, 1]
    if ENABLE_CHEF_LEARNING and simulation_data["learning_effect"]:
        learned_rounds = [r for (is_learned, r) in simulation_data["learning_effect"] if is_learned]
        not_learned_rounds = [r for (is_learned, r) in simulation_data["learning_effect"] if not is_learned]
        
        if learned_rounds and not_learned_rounds:
            labels = ['探针匹配(已学习)', '二分匹配(未学习)']
            means = [np.mean(learned_rounds), np.mean(not_learned_rounds)]
            stds = [np.std(learned_rounds), np.std(not_learned_rounds)]
            
            ax4.bar(labels, means, yerr=stds, capsize=10, color=['lightgreen', 'salmon'], alpha=0.7)
            ax4.set_ylabel('平均匹配轮次')
            ax4.set_title('学习前后匹配效率对比')
            ax4.grid(axis='y', alpha=0.3)
            
            # 添加数值标签
            for i, v in enumerate(means):
                ax4.text(i, v + 0.1, f'{v:.1f}轮', ha='center', fontweight='bold')
            
            # 计算提升效率
            improvement = (means[1] - means[0]) / means[1] * 100
            ax4.text(0.5, 0.9, f'学习提升效率: {improvement:.1f}%', ha='center', va='center', transform=ax4.transAxes, 
                     bbox=dict(facecolor='yellow', alpha=0.5), fontweight='bold')
        else:
            ax4.text(0.5, 0.5, '学习效果数据不足', ha='center', va='center', transform=ax4.transAxes, fontsize=12)
    else:
        ax4.text(0.5, 0.5, '学习功能已关闭', ha='center', va='center', transform=ax4.transAxes, fontsize=12)
    
    # 图5：全局知识库增长曲线
    ax5 = axes[2, 0]
    for chef_id, growth_data in simulation_data["chef_knowledge_growth"].items():
        times, sizes = zip(*growth_data)
        ax5.plot(times, sizes, marker='o', label=f'厨师_{chef_id:02d}', linewidth=2)
    
    ax5.set_xlabel('仿真时间(秒)')
    ax5.set_ylabel('本地知识库大小(匹配关系数)')
    ax5.set_title('各厨师本地知识库增长曲线')
    ax5.legend()
    ax5.grid(alpha=0.3)
    
    # 图6：顾客-厨师服务关系图
    ax6 = axes[2, 1]
    if simulation_data["customer_chef_mapping"]:
        customer_ids = list(simulation_data["customer_chef_mapping"].keys())
        chef_ids = list(set([cid for chefs in simulation_data["customer_chef_mapping"].values() for cid in chefs]))
        
        # 创建邻接矩阵
        adj_matrix = np.zeros((len(customer_ids), len(chef_ids)))
        for i, cust_id in enumerate(customer_ids):
            for chef_id in simulation_data["customer_chef_mapping"][cust_id]:
                j = chef_ids.index(chef_id)
                adj_matrix[i, j] = 1
        
        im = ax6.imshow(adj_matrix, cmap='Blues', aspect='auto')
        ax6.set_xticks(np.arange(len(chef_ids)))
        ax6.set_yticks(np.arange(len(customer_ids)))
        ax6.set_xticklabels([f'厨师_{cid:02d}' for cid in chef_ids])
        ax6.set_yticklabels([f'顾客_{cid:02d}' for cid in customer_ids])
        ax6.set_title('顾客-厨师服务关系热力图')
        plt.setp(ax6.get_xticklabels(), rotation=45, ha="right", rotation_mode="anchor")
        
        # 添加数值标签
        for i in range(len(customer_ids)):
            for j in range(len(chef_ids)):
                text = ax6.text(j, i, int(adj_matrix[i, j]),
                               ha="center", va="center", color="black")
    else:
        ax6.text(0.5, 0.5, '无服务关系数据', ha='center', va='center', transform=ax6.transAxes, fontsize=12)
    
    # 图7：匹配准确率
    ax7 = axes[3, 0]
    if simulation_data["match_accuracy"]:
        correct = sum(1 for _, is_correct in simulation_data["match_accuracy"] if is_correct)
        total = len(simulation_data["match_accuracy"])
        accuracy = correct / total * 100
        
        labels = ['正确匹配', '错误匹配']
        sizes = [correct, total - correct]
        colors = ['lightgreen', 'salmon']
        
        ax7.pie(sizes, labels=labels, colors=colors, autopct='%1.1f%%', startangle=90)
        ax7.set_title(f'整体匹配准确率: {accuracy:.1f}%')
    else:
        ax7.text(0.5, 0.5, '无匹配准确率数据', ha='center', va='center', transform=ax7.transAxes, fontsize=12)
    
    # 图8：轮次分布直方图
    ax8 = axes[3, 1]
    if simulation_data["customer_rounds"]:
        rounds = list(simulation_data["customer_rounds"].values())
        ax8.hist(rounds, bins=range(1, max(rounds)+2), edgecolor='black', alpha=0.7)
        ax8.axvline(x=theoretical_avg_rounds, color='orange', linestyle='--', label=f'理论平均值({theoretical_avg_rounds:.2f})')
        ax8.axvline(x=np.mean(rounds), color='green', linestyle='-', label=f'实际平均值({np.mean(rounds):.2f})')
        ax8.set_xlabel('匹配轮次')
        ax8.set_ylabel('顾客数量')
        ax8.set_title('匹配轮次分布直方图')
        ax8.legend()
        ax8.grid(axis='y', alpha=0.3)
    else:
        ax8.text(0.5, 0.5, '无轮次分布数据', ha='center', va='center', transform=ax8.transAxes, fontsize=12)
    
    plt.tight_layout()
    plt.subplots_adjust(top=0.95)
    plt.savefig('simulation_analysis_v5.png', dpi=300, bbox_inches='tight')
    safe_print("\n📊 分析图表已保存为: simulation_analysis_v5.png", "cyan")

# ================= 主程序与测试 =================
if __name__ == "__main__":
    print(f"==== 多厨师组多顾客随机进入匹配协议仿真 (N={N}) ====")
    print(f"厨师学习功能: {'开启' if ENABLE_CHEF_LEARNING else '关闭'}")
    print(f"全局共享知识库: {'开启' if ENABLE_SHARED_KNOWLEDGE else '关闭'}")
    print(f"本地知识库容量: {LOCAL_KNOWLEDGE_CAPACITY} 个匹配关系")
    print(f"知识库同步延迟概率: {KNOWLEDGE_SYNC_DELAY_PROB*100:.1f}%")
    print(f"顾客到达间隔: {CUSTOMER_ARRIVAL_MIN_DELAY}-{CUSTOMER_ARRIVAL_MAX_DELAY}秒\n")
    
    # 生成全局数据
    recipes, menus, true_mapping = generate_data(N)
    theoretical_worst_rounds = math.ceil(math.log2(N))
    theoretical_avg_rounds = math.log2(N)
    print(f"[系统] 生成了 {N} 道菜品的菜谱和菜单")
    print(f"[系统] 理论最坏情况匹配轮次: {theoretical_worst_rounds}")
    print(f"[系统] 理论平均情况匹配轮次: {theoretical_avg_rounds:.2f}\n")
    
    # 灵活配置厨师组-顾客组映射关系
    chef_customer_groups = [
        ({1}, {1, 2}),              # 厨师1单独服务顾客1、2
        ({2}, {3}),                 # 厨师2单独服务顾客3
        ({3, 4}, {4, 5, 6, 7}),     # 厨师3、4共同服务顾客4、5、6、7
        ({1, 4, 5}, {8, 9})         # 厨师1、4、5共同服务顾客8、9
    ]
    
    # 提取所有顾客ID和厨师ID
    all_customer_ids = set()
    all_chef_ids = set()
    for chef_group, customer_group in chef_customer_groups:
        all_customer_ids.update(customer_group)
        all_chef_ids.update(chef_group)
    
    all_customer_ids = sorted(list(all_customer_ids))
    all_chef_ids = sorted(list(all_chef_ids))
    NUM_CUSTOMERS = len(all_customer_ids)
    NUM_CHEFS = len(all_chef_ids)
    
    print(f"[系统] 配置: {NUM_CHEFS} 位厨师组成 {len(chef_customer_groups)} 个厨师组")
    print(f"[系统] 服务 {NUM_CUSTOMERS} 位顾客")
    for i, (chef_group, customer_group) in enumerate(chef_customer_groups):
        print(f"[系统] 厨师组{i+1}: 厨师{chef_group} 负责顾客{customer_group}")
    print()
    
    # 【修复：正确的全局状态重置逻辑】
    global_customer_states.clear()
    shared_knowledge.clear()
    
    # 字典类型
    simulation_data["customer_rounds"].clear()
    simulation_data["chef_total_rounds"].clear()
    simulation_data["chef_customers_served"].clear()
    simulation_data["chef_knowledge_growth"].clear()
    simulation_data["customer_chef_mapping"].clear()
    
    # 列表类型
    simulation_data["learning_effect"].clear()
    simulation_data["match_accuracy"].clear()
    
    # 整数类型
    simulation_data["probe_success_count"] = 0
    simulation_data["probe_total_count"] = 0
    
    # 为每个厨师组创建独立队列
    chef_group_queues: Dict[frozenset[int], queue.Queue] = {}
    for chef_group, _ in chef_customer_groups:
        group_key = frozenset(chef_group)
        chef_group_queues[group_key] = queue.Queue()
    
    # 创建顾客响应队列
    customer_response_queues = {cid: queue.Queue() for cid in all_customer_ids}
    
    # 启动所有厨师线程
    chef_threads = {}
    for chef_id in all_chef_ids:
        chef_thread = threading.Thread(
            target=chef_worker,
            args=(chef_id, menus, chef_group_queues, customer_response_queues, true_mapping)
        )
        chef_threads[chef_id] = chef_thread
        chef_thread.start()
    
    # 启动顾客线程（随机到达时间）
    customer_threads = {}
    for customer_id in all_customer_ids:
        # 随机等待一段时间模拟顾客到达
        arrival_delay = random.uniform(CUSTOMER_ARRIVAL_MIN_DELAY, CUSTOMER_ARRIVAL_MAX_DELAY)
        time.sleep(arrival_delay)
        
        # 找到该顾客所属的所有厨师组
        assigned_chef_groups = set()
        for chef_group, customer_group in chef_customer_groups:
            if customer_id in customer_group:
                assigned_chef_groups.update(chef_group)
        
        # 随机选择目标菜谱
        target_recipe = random.choice(recipes)
        
        # 启动顾客线程
        cust_thread = threading.Thread(
            target=customer_worker,
            args=(customer_id, target_recipe, recipes, 
                  chef_group_queues, 
                  customer_response_queues[customer_id], 
                  assigned_chef_groups,
                  true_mapping)
        )
        customer_threads[customer_id] = cust_thread
        cust_thread.start()
    
    # 等待所有顾客完成
    for cust_thread in customer_threads.values():
        cust_thread.join()
    
    # 通知所有厨师退出
    for group_queue in chef_group_queues.values():
        group_queue.put(None)
    
    # 等待所有厨师线程结束
    for chef_thread in chef_threads.values():
        chef_thread.join()
    
    # 统计结果
    print("\n" + "="*80)
    print("📊 仿真结果统计")
    print("="*80)
    print(f"总菜品数: {N}")
    print(f"厨师数量: {NUM_CHEFS}")
    print(f"厨师组数量: {len(chef_customer_groups)}")
    print(f"顾客数量: {NUM_CUSTOMERS}")
    print(f"理论最坏情况匹配轮次: {theoretical_worst_rounds}")
    print(f"理论平均情况匹配轮次: {theoretical_avg_rounds:.2f}")
    
    if simulation_data["customer_rounds"]:
        avg_rounds = np.mean(list(simulation_data["customer_rounds"].values()))
        max_rounds = max(simulation_data["customer_rounds"].values())
        min_rounds = min(simulation_data["customer_rounds"].values())
        success_rate = len(simulation_data["customer_rounds"]) / NUM_CUSTOMERS * 100
        print(f"\n顾客匹配轮次统计:")
        print(f"  成功点餐率: {success_rate:.1f}%")
        print(f"  平均轮次: {avg_rounds:.2f}")
        print(f"  最快轮次: {min_rounds}")
        print(f"  最慢轮次: {max_rounds}")
        print(f"  与理论最坏值偏差: {(avg_rounds - theoretical_worst_rounds)/theoretical_worst_rounds*100:.1f}%")
    
    if simulation_data["probe_total_count"] > 0:
        probe_success = simulation_data["probe_success_count"]
        probe_total = simulation_data["probe_total_count"]
        probe_success_rate = probe_success / probe_total * 100
        print(f"\n探针匹配统计:")
        print(f"  探针总次数: {probe_total}")
        print(f"  探针成功次数: {probe_success}")
        print(f"  探针成功率: {probe_success_rate:.1f}%")
    
    print(f"\n厨师负载统计:")
    for chef_id in all_chef_ids:
        total_rounds = simulation_data["chef_total_rounds"].get(chef_id, 0)
        customers_served = simulation_data["chef_customers_served"].get(chef_id, 0)
        avg_rounds_per_customer = total_rounds / customers_served if customers_served > 0 else 0
        print(f"  厨师_{chef_id:02d}: 服务{customers_served}位顾客，总处理{total_rounds}轮，平均{avg_rounds_per_customer:.2f}轮/顾客")
    
    if ENABLE_CHEF_LEARNING:
        total_local_knowledge = sum([data[-1][1] for data in simulation_data["chef_knowledge_growth"].values()])
        print(f"\n学习效果统计:")
        print(f"  所有厨师本地知识库总大小: {total_local_knowledge} 个匹配关系")
        if ENABLE_SHARED_KNOWLEDGE:
            print(f"  全局共享知识库大小: {len(shared_knowledge)} 个匹配关系")
        
        if simulation_data["learning_effect"]:
            learned_rounds = [r for (is_learned, r) in simulation_data["learning_effect"] if is_learned]
            not_learned_rounds = [r for (is_learned, r) in simulation_data["learning_effect"] if not is_learned]
            
            if learned_rounds and not_learned_rounds:
                avg_learned = np.mean(learned_rounds)
                avg_not_learned = np.mean(not_learned_rounds)
                improvement = (avg_not_learned - avg_learned) / avg_not_learned * 100
                print(f"  探针匹配平均轮次: {avg_learned:.2f}")
                print(f"  二分匹配平均轮次: {avg_not_learned:.2f}")
                print(f"  学习提升效率: {improvement:.1f}%")
    
    if simulation_data["match_accuracy"]:
        correct = sum(1 for _, is_correct in simulation_data["match_accuracy"] if is_correct)
        total = len(simulation_data["match_accuracy"])
        accuracy = correct / total * 100
        print(f"\n匹配准确率统计:")
        print(f"  正确匹配数: {correct}")
        print(f"  错误匹配数: {total - correct}")
        print(f"  整体准确率: {accuracy:.1f}%")
    
    # 生成分析图表
    generate_analysis_charts(theoretical_worst_rounds, theoretical_avg_rounds, chef_customer_groups)
    
    print("\n✅ 仿真完成！")
    if len(simulation_data["customer_rounds"]) == NUM_CUSTOMERS and all(is_correct for _, is_correct in simulation_data["match_accuracy"]):
        print("🎉 所有顾客均成功完成点餐且匹配100%正确！")
        if simulation_data["probe_success_count"] > 0:
            print(f"🚀 学习功能表现出色！探针成功率达到 {simulation_data['probe_success_count']/simulation_data['probe_total_count']*100:.1f}%")
    else:
        print(f"⚠️  有 {NUM_CUSTOMERS - len(simulation_data['customer_rounds'])} 位顾客点餐失败，{total - correct} 位顾客匹配错误。")