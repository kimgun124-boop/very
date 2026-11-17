import collections,sys
c=collections.Counter(); tot=0
for line in open(sys.argv[1]):
    stack,n=line.rsplit(' ',1); n=int(n)
    if 'render_board' not in stack or 'render_tab_area' in stack: continue
    tot+=n
    for f in set(f.split(' (')[0]+':'+f.split(':')[-1].rstrip(')') for f in stack.split(';') if '(app.py' in f or '(data.py' in f):
        c[f]+=n
print("board samples(50Hz)",tot)
for k,n in c.most_common(40): print(n,k)
