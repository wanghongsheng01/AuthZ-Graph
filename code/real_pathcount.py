import numpy as np, sys, math
d=sys.argv[1]
t=np.fromfile(d+"/types.bin",dtype=np.int8); s=np.fromfile(d+"/e_src.bin",dtype=np.int32); e=np.fromfile(d+"/e_dst.bin",dtype=np.int32)
n=len(t); g=(t[s]==1)&(t[e]==1); ss,dd=s[g],e[g]
o=np.argsort(ss,kind="stable"); ss,dd=ss[o],dd[o]; ptr=np.zeros(n+1,np.int64); np.add.at(ptr,ss+1,1); ptr=np.cumsum(ptr)
# number of upward paths from each group to any sink: P(x)=1+sum P(parent); ids: parents have lower ids -> ascending order
logP=np.zeros(n)
groups=np.where(t==1)[0]
for x in groups:
    ch=dd[ptr[x]:ptr[x+1]]
    if len(ch)==0: logP[x]=0.0
    else:
        m=logP[ch].max(); logP[x]=m+math.log1p(np.exp(logP[ch]-m).sum()-1+np.exp(-m)) if True else 0
print("groups",len(groups),"max log2(paths)",logP[groups].max()/math.log(2),"median",np.median(logP[groups])/math.log(2),
      "frac > 2^63", float((logP[groups]/math.log(2)>63).mean()))
