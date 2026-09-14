"""Is residual AUC < 0.5 evidence of anti-predictiveness, or a mechanical artefact?

Simulate a model that is BY CONSTRUCTION a noisy, shrunk copy of the true market
probability: zero incremental information, no inverse signal. If residual AUC
still lands below 0.5, then residual AUC < 0.5 says nothing about direction.
"""
import math, random
random.seed(7)
def auc(sc, ys):
    p = sorted(zip(sc, ys)); n = len(p); rk = {}; i = 0
    while i < n:
        j = i
        while j+1 < n and p[j+1][0] == p[i][0]: j += 1
        r = (i+j)/2+1
        for k in range(i, j+1): rk[k] = r
        i = j+1
    pos = sum(y for _, y in p); neg = n-pos
    return (sum(rk[k] for k,(_,y) in enumerate(p) if y==1)-pos*(pos+1)/2)/(pos*neg)
sig = lambda z: 1/(1+math.exp(-z))
N = 40000
print(f"  {'construction':46s} {'model AUC':>10s} {'residual AUC':>13s}")
for shrink, noise in ((0.7, 0.0), (0.7, 0.5), (0.5, 0.8), (1.0, 0.8)):
    mk, md, ys = [], [], []
    for _ in range(N):
        lg = random.gauss(-0.25, 0.9)          # true market logit
        p_mkt = sig(lg)
        y = 1 if random.random() < p_mkt else 0   # market IS truth
        p_mod = sig(shrink*lg + (random.gauss(0, noise) if noise else 0))
        mk.append(p_mkt); md.append(p_mod); ys.append(y)
    lab = f"model = sigmoid({shrink}*logit(mkt)" + (f" + N(0,{noise}))" if noise else ")")
    print(f"  {lab:46s} {auc(md,ys):10.4f} {auc([a-b for a,b in zip(md,mk)],ys):13.4f}")
print("\n  OBSERVED in the real residual test: model AUC 0.6409, residual AUC 0.3775")
