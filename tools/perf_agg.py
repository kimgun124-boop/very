import collections,sys
tabs=collections.Counter(); sub=collections.defaultdict(collections.Counter); oth=collections.Counter()
for line in open(sys.argv[1]):
    stack,n=line.rsplit(' ',1); n=int(n)
    fr=stack.split(';'); names=[f.split(' (')[0] for f in fr]
    if 'render_tab_area' in stack:
        r=[i for i,x in enumerate(names) if x.startswith('render_') and x not in ('render_tab_area','render_board','render_board.<locals>.<lambda>')]
        t=names[r[0]] if r else ('main_nav' if 'main_nav' in names else 'tabarea-other')
        tabs[t]+=n
        mine=[i for i,f in enumerate(fr) if ('(app.py' in f or '(data.py' in f or '/very/' in f)]
        j=mine[-1] if mine else 0; lab=fr[j].split(' (')[0]+':'+fr[j].split(':')[-1].rstrip(')')
        lib = fr[j+1].split(' (')[0] if j+1<len(fr) else 'self'
        sub[t][lab+' -> '+lib]+=n
    elif 'render_board' in stack: oth['BOARD']+=n
    elif 'ScriptRunner' in stack: oth['script-other']+=n
    else: oth['bg/idle']+=n
print({k:v/50 for k,v in oth.items()})
for t,n in tabs.most_common(int(sys.argv[2]) if len(sys.argv)>2 else 18):
    print(f"\n{t} {n/50:.2f}s")
    for f,m in sub[t].most_common(6): print("   ",m,f[:150])
