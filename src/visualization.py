# visualization.py
"""
根据收集的仿真数据生成多维度分析图表（已优化时序与分布可视化）。
"""

import numpy as np
import matplotlib.pyplot as plt
from config import ENABLE_CHEF_LEARNING, ENABLE_SHARED_KNOWLEDGE
import global_state as gs
from utils import safe_print

def generate_analysis_charts(theoretical_worst_rounds: float, theoretical_avg_rounds: float):
    """
    生成所有图表并保存为 simulation_analysis.png
    """
    plt.rcParams['font.sans-serif'] = ['SimHei']
    plt.rcParams['axes.unicode_minus'] = False

    fig, axes = plt.subplots(4, 2, figsize=(16, 24))
    fig.suptitle('多厨师组多顾客匹配协议仿真分析 (系统动态演进)', fontsize=18, fontweight='bold')

    # ==========================================
    # 图1 (0,0): 匹配轮次随服务时序的演进 (替代密集的柱状图)
    # ==========================================
    ax1 = axes[0, 0]
    if gs.simulation_data["learning_effect"]:
        # learning_effect 是按顾客完成顺序追加的
        rounds_seq = [r for _, r in gs.simulation_data["learning_effect"]]
        x_seq = range(1, len(rounds_seq) + 1)
        
        # 添加微小的随机扰动，展示数据密度
        y_jitter = np.array(rounds_seq) + np.random.uniform(-0.15, 0.15, len(rounds_seq))
        ax1.scatter(x_seq, y_jitter, alpha=0.3, color='skyblue', s=20, label='单客实际轮次(视觉加扰)')
        
        # 计算移动平均线以展示趋势
        window = max(1, len(rounds_seq) // 10)
        if len(rounds_seq) >= window:
            rolling_avg = np.convolve(rounds_seq, np.ones(window)/window, mode='valid')
            ax1.plot(range(window, len(rounds_seq) + 1), rolling_avg, color='red', linewidth=2, label=f'{window}客移动平均')
        
        ax1.axhline(y=theoretical_worst_rounds, color='orange', linestyle='--', label=f'理论最坏({theoretical_worst_rounds})')
        ax1.set_xlabel('完成服务时序 (顾客顺序)')
        ax1.set_ylabel('匹配交互轮次')
        ax1.set_title('图1: 匹配效率演进趋势 (体现学习降本)')
        ax1.legend()
        ax1.grid(alpha=0.3)
    else:
        ax1.text(0.5, 0.5, '无轮次数据', ha='center', va='center', transform=ax1.transAxes)

    # ==========================================
    # 图2 (0,1): 累计探针命中率演进趋势 (替代0/100%的饼图)
    # ==========================================
    ax2 = axes[0, 1]
    if gs.simulation_data["learning_effect"]:
        hits = [1 if learned else 0 for learned, _ in gs.simulation_data["learning_effect"]]
        cum_hits = np.cumsum(hits)
        cum_rate = cum_hits / np.arange(1, len(hits) + 1) * 100
        
        ax2.plot(range(1, len(cum_rate) + 1), cum_rate, color='green', linewidth=2)
        ax2.set_xlabel('总探针发起次数')
        ax2.set_ylabel('累计命中率 (%)')
        ax2.set_title('图2: 系统探针命中率演进曲线')
        ax2.set_ylim(-5, 105)
        ax2.grid(alpha=0.3)
        if len(cum_rate) > 0:
            ax2.text(len(cum_rate)*0.95, cum_rate[-1]+5, f'最终: {cum_rate[-1]:.1f}%', ha='right', fontweight='bold', color='green')
    else:
        ax2.text(0.5, 0.5, '无探针数据', ha='center', va='center', transform=ax2.transAxes)

    # ==========================================
    # 图3 (1,0): 厨师总处理轮次与服务顾客数对比
    # ==========================================
    ax3 = axes[1, 0]
    chef_ids = list(gs.simulation_data["chef_total_rounds"].keys())
    if chef_ids:
        total_rounds = [gs.simulation_data["chef_total_rounds"][cid] for cid in chef_ids]
        customers_served = [gs.simulation_data["chef_customers_served"].get(cid, 0) for cid in chef_ids]
        x = np.arange(len(chef_ids))
        width = 0.35
        ax3.bar(x - width/2, total_rounds, width, label='总处理轮次', color='orange', alpha=0.7)
        ax3.bar(x + width/2, customers_served, width, label='服务顾客数', color='green', alpha=0.7)
        ax3.set_xticks(x)
        ax3.set_xticklabels([f'厨师_{cid:02d}' for cid in chef_ids])
        ax3.set_title('图3: 各厨师宏观负载对比')
        ax3.legend()
        ax3.grid(axis='y', alpha=0.3)

    # ==========================================
    # 图4 (1,1): 探针与二分匹配的轮次分布 (替代效果柱状图)
    # ==========================================
    ax4 = axes[1, 1]
    if ENABLE_CHEF_LEARNING and gs.simulation_data["learning_effect"]:
        learned_r = [r for l, r in gs.simulation_data["learning_effect"] if l]
        not_learned_r = [r for l, r in gs.simulation_data["learning_effect"] if not l]
        
        data = []
        labels = []
        if not_learned_r:
            data.append(not_learned_r)
            labels.append(f'二分匹配 (未命中)\nn={len(not_learned_r)}')
        if learned_r:
            data.append(learned_r)
            labels.append(f'探针匹配 (命中)\nn={len(learned_r)}')
            
        if data:
            box = ax4.boxplot(data, labels=labels, patch_artist=True, widths=0.4)
            colors = ['salmon', 'lightgreen']
            for patch, color in zip(box['boxes'], colors[:len(data)]):
                patch.set_facecolor(color)
                patch.set_alpha(0.7)
            
            if learned_r:
                pos = 2 if not_learned_r else 1
                ax4.text(pos, 1.2, "方差为0\n(恒定1轮)", color='darkgreen', ha='center', fontsize=10, fontweight='bold')
            
            # 添加叠加散点(Swarm近似)以展示真实分布
            for i, d in enumerate(data):
                y = d
                x = np.random.normal(i + 1, 0.05, size=len(y))  # X轴微小抖动
                ax4.scatter(x, y, alpha=0.5, color='darkgray', s=15, zorder=3)

            ax4.set_ylabel('匹配所需轮次')
            ax4.set_title('图4: 算法路径轮次分布 (箱线+分布散点图)')
            ax4.grid(axis='y', alpha=0.3)
    else:
        ax4.text(0.5, 0.5, '数据不足或未开启学习', ha='center', va='center', transform=ax4.transAxes)

    # ==========================================
    # 图5 (2,0): 各厨师本地知识库增长曲线
    # ==========================================
    ax5 = axes[2, 0]
    has_growth = False
    for chef_id, growth in gs.simulation_data["chef_knowledge_growth"].items():
        if growth and len(growth) > 1:
            has_growth = True
            times, sizes = zip(*growth)
            ax5.plot(times, sizes, marker='o', markersize=3, label=f'厨师_{chef_id:02d}', linewidth=2)
    if has_growth:
        ax5.set_xlabel('仿真时间 (秒)')
        ax5.set_ylabel('本地知识库条目数')
        ax5.set_title('图5: 厨师本地知识库增长曲线')
        ax5.legend()
        ax5.grid(alpha=0.3)
    else:
         ax5.text(0.5, 0.5, '无知识增长数据', ha='center', va='center', transform=ax5.transAxes)

    # ==========================================
    # 图6 (2,1): 顾客-厨师服务关系热力图
    # ==========================================
    ax6 = axes[2, 1]
    if gs.simulation_data["customer_chef_mapping"]:
        raw_cust_ids = list(gs.simulation_data["customer_chef_mapping"].keys())
        chef_ids_all = sorted(list(set([cid for chefs in gs.simulation_data["customer_chef_mapping"].values() for cid in chefs])))
        
        # 按照负责该顾客的“主厨师 ID”对顾客进行排序，形成视觉上的聚类对角线
        cust_ids_sorted = sorted(raw_cust_ids, key=lambda c: gs.simulation_data["customer_chef_mapping"][c][0] if gs.simulation_data["customer_chef_mapping"][c] else 0)
        
        adj = np.zeros((len(cust_ids_sorted), len(chef_ids_all)))
        for i, cid in enumerate(cust_ids_sorted):
            for chid in gs.simulation_data["customer_chef_mapping"][cid]:
                j = chef_ids_all.index(chid)
                adj[i, j] = 1
        
        im = ax6.imshow(adj, cmap='Blues', aspect='auto')
        ax6.set_xticks(np.arange(len(chef_ids_all)))
        ax6.set_yticks(np.arange(len(cust_ids_sorted)))
        ax6.set_xticklabels([f'厨师_{cid:02d}' for cid in chef_ids_all])
        
        if len(cust_ids_sorted) > 20:
            ax6.set_yticks([])
            ax6.set_ylabel(f'{len(cust_ids_sorted)} 位顾客 (已按服务组聚类排序)')
        else:
            ax6.set_yticklabels([f'顾客_{cid:02d}' for cid in cust_ids_sorted])
            
        ax6.set_title('图6: 顾客-厨师服务关系热力图 (聚类视效)')
    else:
        ax6.text(0.5, 0.5, '无服务关系数据', ha='center', va='center', transform=ax6.transAxes)

    # ==========================================
    # 图7 (3,0): 厨师平均单客接待开销 (替代无聊的准确率饼图)
    # ==========================================
    ax7 = axes[3, 0]
    if chef_ids:
        avg_overhead = []
        for cid in chef_ids:
            served = gs.simulation_data["chef_customers_served"].get(cid, 0)
            tr = gs.simulation_data["chef_total_rounds"].get(cid, 0)
            avg_overhead.append(tr / served if served > 0 else 0)
            
        ax7.bar([f'厨师_{cid:02d}' for cid in chef_ids], avg_overhead, color='mediumpurple', alpha=0.7)
        ax7.axhline(y=theoretical_avg_rounds, color='red', linestyle='--', label=f'理论平均轮次 ({theoretical_avg_rounds:.1f})')
        ax7.set_ylabel('平均每单处理轮次')
        ax7.set_title('图7: 厨师微观效能 (平均单客接待开销)')
        for i, v in enumerate(avg_overhead):
            ax7.text(i, v + 0.1, f'{v:.1f}', ha='center', fontsize=9)
        ax7.legend()
        ax7.grid(axis='y', alpha=0.3)

    # ==========================================
    # 图8 (3,1): 匹配轮次累积分布函数 ECDF (替代单一柱子的直方图)
    # ==========================================
    ax8 = axes[3, 1]
    if gs.simulation_data["customer_rounds"]:
        rounds = sorted(list(gs.simulation_data["customer_rounds"].values()))
        y = np.arange(1, len(rounds) + 1) / len(rounds) * 100
        
        ax8.step(rounds, y, where='post', color='darkcyan', linewidth=2)
        ax8.fill_between(rounds, y, step="post", alpha=0.2, color='cyan')
        ax8.axvline(x=theoretical_worst_rounds, color='orange', linestyle='--', label=f'理论最坏 ({theoretical_worst_rounds})')
        ax8.set_xlabel('匹配轮次')
        ax8.set_ylabel('完成比例 (%)')
        ax8.set_title('图8: 匹配轮次累积分布 (ECDF)')
        ax8.set_ylim(0, 105)
        ax8.legend(loc='lower right')
        ax8.grid(alpha=0.3)
    else:
        ax8.text(0.5, 0.5, '无轮次数据', ha='center', va='center', transform=ax8.transAxes)

    plt.tight_layout()
    plt.subplots_adjust(top=0.95)
    plt.savefig('simulation_analysis.png', dpi=300, bbox_inches='tight')
    safe_print("\n📊 深度分析图表已保存为 simulation_analysis.png", "cyan")