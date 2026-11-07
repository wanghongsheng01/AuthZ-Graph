# Stream the UCI Amazon Access Samples archive (no extraction to disk) into a compact pickle.
import tarfile, csv, io, sys, pickle, collections
tgz = sys.argv[1]; out = sys.argv[2]
tf = tarfile.open(tgz, 'r:gz')
persons = []; hist = []
for m in tf:
    f = io.TextIOWrapper(tf.extractfile(m), newline='')
    if 'history' in m.name:
        r = csv.reader(f); hdr = next(r)
        for row in r: hist.append(row)
    else:
        r = csv.reader(f); hdr = next(r)
        pidx = [i for i, h in enumerate(hdr) if h.startswith('PERSON_')]
        aidx = [i for i, h in enumerate(hdr) if not h.startswith('PERSON_')]
        for k, row in enumerate(r):
            attrs = {hdr[i]: row[i] for i in pidx}
            acc = [hdr[i] for i in aidx if row[i] not in ('0', '')]
            persons.append((attrs, acc))
            if k % 5000 == 0: print(k, len(acc), file=sys.stderr, flush=True)
pickle.dump({'persons': persons, 'hist': hist, 'hist_hdr': ['ACTION','TARGET_NAME','LOGIN','REQUEST_DATE','AUTHORIZATION_DATE']}, open(out, 'wb'))
c = collections.Counter(a.split('_')[0] for _, acc in persons for a in acc)
print('persons', len(persons), 'grants', sum(len(a) for _, a in persons), c, 'hist', len(hist))
print('sample attrs', persons[0][0]); print('hist sample', hist[:3])
