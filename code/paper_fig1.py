# Fig. 1 (running example), executed by code/paper_facts.py (needs plt and PAPER); matplotlib because the TeX installation has no TikZ.
# Fig 1: running example (drawn with matplotlib; the TeX installation has no TikZ)
from matplotlib.patches import FancyBboxPatch
fig, ax = plt.subplots(figsize=(3.45, 1.05))
ax.set_xlim(0, 10); ax.set_ylim(0, 3); ax.axis('off')
# node kinds are told apart by the outline colour; white fills keep every label off a coloured shape
N = {'Alice': (0.6, 2.2, '#1f4fa8'), 'team-A': (2.35, 2.2, '#2e7d4f'), 'Sales': (4.0, 2.2, '#2e7d4f'),
     'Analyst': (5.6, 2.2, '#c0601a'), 'orders': (8.7, 2.2, '#555555'), 'orders.cost': (8.7, 0.6, '#555555'),
     'Contractors': (2.6, 0.6, '#2e7d4f')}
for k, (x0, y0, col) in N.items():
    w = 0.5 + 0.118 * len(k)
    ax.add_patch(FancyBboxPatch((x0 - w / 2, y0 - 0.28), w, 0.56, boxstyle='round,pad=0.02,rounding_size=0.12',
                                fc='white', ec=col, lw=0.9))
    ax.text(x0, y0, k, ha='center', va='center', fontsize=6.6, family='monospace' if k.startswith('orders') else 'serif')
def arrow(a, b, col='#333333', ls='-', lw=0.8, txt=None, tx=0, ty=0.18):
    (x1, y1, _), (x2, y2, _) = N[a], N[b]
    w1 = (0.5 + 0.118 * len(a)) / 2; w2 = (0.5 + 0.118 * len(b)) / 2
    if y1 == y2: s, e = (x1 + w1, y1), (x2 - w2, y2)
    elif x1 == x2: s, e = (x1, y1 - 0.28), (x2, y2 + 0.28)
    else: s, e = (x1 + w1, y1), (x2 - w2, y2)
    ax.annotate('', xy=e, xytext=s, arrowprops=dict(arrowstyle='-|>', color=col, lw=lw, ls=ls, mutation_scale=7))
    if txt: ax.text((s[0] + e[0]) / 2 + tx, (s[1] + e[1]) / 2 + ty, txt, ha='center', va='bottom', fontsize=6, color=col)
arrow('Alice', 'team-A'); arrow('team-A', 'Sales'); arrow('Sales', 'Analyst')
arrow('Analyst', 'orders', txt='net=corp'); arrow('orders', 'orders.cost')
arrow('Contractors', 'orders.cost', col='#b3202a', lw=1.1)
ax.text((N['Contractors'][0] + N['orders.cost'][0]) / 2, 0.65, 'deny', ha='center', va='bottom', fontsize=6, fontweight='bold', color='#333333')
ax.annotate('', xy=(N['Contractors'][0] - 0.95, 0.6), xytext=(0.6, 1.92),
            arrowprops=dict(arrowstyle='-|>', color='#1f4fa8', lw=1.0, ls='--', mutation_scale=7,
                            connectionstyle='angle,angleA=-90,angleB=180,rad=0'))
ax.text(0.68, 1.05, 'insert', fontsize=6, color='#1f4fa8', ha='left')
fig.tight_layout(pad=0.1)
fig.savefig(f'{PAPER}/figs/example.pdf')
plt.close(fig)
