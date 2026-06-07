import threading
import queue
import random
import time
import math

# ================= 系统核心参数 =================
N = 64  # 支持任意规模
SIMULATION_SPEED = 0.1 # 打印延迟(秒)

# 线程通信通道
q_to_chef = queue.Queue()
q_to_cust = queue.Queue()
print_lock = threading.Lock()

def safe_print(msg, color=""):
    """带锁的安全打印，防止多线程输出错乱"""
    colors = {"blue": "\033[94m", "green": "\033[92m", "yellow": "\033[93m", "red": "\033[91m", "end": "\033[0m"}
    c_start = colors.get(color, "")
    c_end = colors["end"] if color else ""
    with print_lock:
        print(f"{c_start}{msg}{c_end}")
        time.sleep(SIMULATION_SPEED)

# ================= 智能数据生成 =================
def generate_data(n):
    bits = math.ceil(math.log2(n))
    # 明确区分【主材】（核心特征，必然上菜单）和【辅料】（噪音特征，可能不上菜单）
    core_ings = [f"[主材]核心{i+1}" for i in range(bits)]
    noise_ings = [f"[辅料]配菜{i}" for i in range(30)]
    
    recipes, menus, secret_mapping = [], [], {}
    
    for i in range(n):
        bin_str = format(i, f'0{bits}b')
        base_ings = [core_ings[j] for j, bit in enumerate(bin_str) if bit == '1']
        
        # 菜谱包含：全部目标主材 + 大量随机辅料
        r_ings = base_ings + random.sample(noise_ings, k=random.randint(3, 6))
        r_id = f"Recipe_{i:02d}"
        recipes.append({"id": r_id, "ings": set(r_ings), "steps": ["步骤1", "步骤2"]})
        
        # 菜单包含：全部目标主材 + 极少数随机辅料 (模拟 ing(M) ⊆ ing(P))
        m_ings = base_ings + (random.sample(r_ings[len(base_ings):], 1) if len(r_ings) > len(base_ings) else [])
        m_id = f"Menu_{i:02d}"
        menus.append({"id": m_id, "name": f"招牌菜_{i:02d}", "price": random.randint(30, 150), "ings": set(m_ings)})
        
        secret_mapping[r_id] = m_id
        
    random.shuffle(recipes)
    random.shuffle(menus)
    return recipes, menus, secret_mapping

# ================= 智能化顾客线程 =================
def customer_worker(target_recipe, all_recipes):
    candidates = all_recipes.copy()
    safe_print(f"[顾客(C)] 目标锁定: {target_recipe['id']} | 食材: {target_recipe['ings']}\n", "blue")
    
    round_num = 1
    used_features = set()
    
    while len(candidates) > 1:
        # 【智能改进 1：过滤策略】顾客知道哪些是客观可查的主材，优先使用主材进行提问
        ing_counts = {}
        for r in candidates:
            for ing in r['ings']:
                if ing not in used_features and ing.startswith("[主材]"):
                    ing_counts[ing] = ing_counts.get(ing, 0) + 1
                    
        # 寻找最能平分当前候选集的特征
        best_ing = None
        min_diff = float('inf')
        for ing, count in ing_counts.items():
            diff = abs(count - len(candidates) / 2)
            if diff < min_diff and 0 < count < len(candidates):
                min_diff = diff
                best_ing = ing
                
        # 异常处理：如果没有主材可用了，只能硬着头皮试辅料
        if not best_ing:
            safe_print("[顾客(C)] ⚠️ 主材特征耗尽，被迫使用辅料进行探索...", "red")
            break # 简化模型中，若发生此情况直接跳出
            
        used_features.add(best_ing)
        has_ing = best_ing in target_recipe['ings']
        
        safe_print(f"--- 第 {round_num} 轮交互 ---", "yellow")
        question = f"我想要的菜，主要食材{'包含' if has_ing else '不包含'}【{best_ing}】，你菜单上有符合的吗？"
        q_to_chef.put(("INCLUDE" if has_ing else "EXCLUDE", best_ing))
        safe_print(f"[顾客(C)] 提问: {question}", "blue")
        
        # 等待厨师确认
        ans = q_to_cust.get()
        
        # 【智能改进 2：状态同步确认】必须根据厨师的回答来更新本地状态，绝不自顾自推演
        if ans == "是":
            if has_ing:
                candidates = [r for r in candidates if best_ing in r['ings']]
            else:
                candidates = [r for r in candidates if best_ing not in r['ings']]
            safe_print(f"[顾客(C)] 收到'是'。同步状态，本地候选菜谱降至: {len(candidates)}", "blue")
        else:
            safe_print(f"[顾客(C)] ❌ 收到'否'！特征【{best_ing}】在菜单端可能已失效。丢弃该特征，回滚状态。", "red")
            # 不更新 candidates，下一轮重新选特征
            
        round_num += 1

    q_to_chef.put(("STOP", None))
    
# ================= 智能化厨师线程 =================
def chef_worker(all_menus):
    candidates = all_menus.copy()
    
    while True:
        cmd, feature = q_to_chef.get()
        if cmd == "STOP":
            break
            
        # 事务预处理：在临时集合上进行过滤
        if cmd == "INCLUDE":
            temp_candidates = [m for m in candidates if feature in m['ings']]
        elif cmd == "EXCLUDE":
            temp_candidates = [m for m in candidates if feature not in m['ings']]
            
        # 【智能改进 3：安全的事务回滚】
        if len(temp_candidates) > 0:
            ans = "是"
            candidates = temp_candidates # 事务提交：更新候选集
            safe_print(f"[厨师(K)] 验证成功，有符合的菜单。回应: {ans}", "green")
        else:
            ans = "否"
            # 事务回滚：保持 candidates 不变，不执行错误剔除
            safe_print(f"[厨师(K)] ⚠️ 验证失败，无任何匹配菜单！拒绝更新候选集。回应: {ans}", "red")
            
        safe_print(f"[厨师(K)] 当前剩余候选菜单数: {len(candidates)}", "green")
        q_to_cust.put(ans)
        
    final_menu = candidates[0] if candidates else None
    if final_menu:
        safe_print(f"\n[厨师(K)] 订单确认！最终锁定菜单: {final_menu['id']}", "green")
        q_to_cust.put(final_menu['id'])

# ================= 运行测试 =================
if __name__ == "__main__":
    print(f"==== 智能化动态匹配协议仿真 (N={N}) ====")
    recipes, menus, true_mapping = generate_data(N)
    target = random.choice(recipes)
    target_true_menu_id = true_mapping[target['id']]
    
    print(f"[上帝视角] 真实匹配关系: {target['id']} <=> {target_true_menu_id}\n")
    
    chef_t = threading.Thread(target=chef_worker, args=(menus,))
    cust_t = threading.Thread(target=customer_worker, args=(target, recipes))
    chef_t.start(); cust_t.start()
    cust_t.join(); chef_t.join()
    
    chef_final_decision = q_to_cust.get()
    print("="*40)
    if chef_final_decision == target_true_menu_id:
        print(f"✅ 匹配成功！双方在严格隔离下准确锁定了目标。")
    else:
        print(f"❌ 匹配失败！")