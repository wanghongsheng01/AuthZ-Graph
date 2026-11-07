#!/usr/bin/env python3
"""Parse real hierarchies into a common form for gen_real.py.

Output directory gets three TSV files:
  groups.tsv   gid<TAB>name
  nest.tsv     child_gid<TAB>parent_gid      (child is contained in parent)
  members.tsv  user<TAB>gid                  (user is a direct member of group)

usage:
  parse_real.py obo  <out> <ontology.obo> <annotations> <gaf|hpo>
  parse_real.py wiki <out> <categorylinks.sql.gz> <page.sql.gz> <linktarget.sql.gz>
"""
import gzip, os, re, sys


def write(out, groups, nest, members):
    os.makedirs(out, exist_ok=True)
    with open(f'{out}/groups.tsv', 'w') as f:
        for g, name in groups.items():
            f.write(f'{g}\t{name}\n')
    with open(f'{out}/nest.tsv', 'w') as f:
        for c, p in nest:
            f.write(f'{c}\t{p}\n')
    with open(f'{out}/members.tsv', 'w') as f:
        for u, g in members:
            f.write(f'{u}\t{g}\n')
    print(out, 'groups', len(groups), 'nest', len(nest), 'members', len(members))


# ---------------------------------------------------------------- OBO ontologies (GO, HPO)
def parse_obo(path):
    terms, nest, cur, obsolete = {}, [], None, set()
    for line in open(path, encoding='utf-8'):
        line = line.rstrip('\n')
        if line.startswith('['):
            cur = {'stanza': line}
            continue
        if cur is None or cur.get('stanza') != '[Term]':
            continue
        if line.startswith('id: '):
            cur['id'] = line[4:].strip()
        elif line.startswith('name: '):
            terms[cur['id']] = line[6:].strip()
        elif line.startswith('is_obsolete: true'):
            obsolete.add(cur['id'])
        elif line.startswith('is_a: '):
            nest.append((cur['id'], line[6:].split('!')[0].strip()))
        elif line.startswith('relationship: part_of '):
            nest.append((cur['id'], line[len('relationship: part_of '):].split('!')[0].strip()))
    for t in obsolete:
        terms.pop(t, None)
    nest = [(c, p) for c, p in nest if c in terms and p in terms]
    return terms, sorted(set(nest))


def cmd_obo(out, obo, ann, kind):
    terms, nest = parse_obo(obo)
    members = set()
    if kind == 'gaf':
        with gzip.open(ann, 'rt', encoding='utf-8', errors='replace') as f:
            for line in f:
                if line.startswith('!'):
                    continue
                x = line.rstrip('\n').split('\t')
                if len(x) < 5 or 'NOT' in x[3]:
                    continue
                if x[4] in terms:
                    members.add((x[2], x[4]))       # gene symbol -> GO term
    else:
        for i, line in enumerate(open(ann, encoding='utf-8')):
            if i == 0:
                continue
            x = line.rstrip('\n').split('\t')
            if len(x) >= 3 and x[2] in terms:
                members.add((x[1], x[2]))           # gene symbol -> HPO term
    write(out, terms, nest, sorted(members))


# ---------------------------------------------------------------- MediaWiki SQL dumps
def sql_rows(path, table):
    """Yield tuples from INSERT statements of a MediaWiki SQL dump (streaming)."""
    pre = f'INSERT INTO `{table}` VALUES'.encode()
    active = False
    with gzip.open(path, 'rb') as f:
        for raw in f:
            if raw.startswith(pre):
                active = True
                raw = raw[len(pre):]
            elif not active:
                continue
            elif raw.startswith(b'INSERT') or raw.startswith(b'/*') or raw.startswith(b'--'):
                active = raw.startswith(pre)
                continue
            s = raw.decode('utf-8', errors='replace')
            if s.rstrip().endswith(';'):
                active = False
            i, n = 0, len(s)
            while i < n:
                if s[i] != '(':
                    i += 1
                    continue
                i += 1
                row, tok, inq = [], [], False
                while i < n:
                    ch = s[i]
                    if inq:
                        if ch == '\\':
                            tok.append(s[i + 1]); i += 2; continue
                        if ch == "'":
                            inq = False
                        else:
                            tok.append(ch)
                        i += 1
                        continue
                    if ch == "'":
                        inq = True
                    elif ch == ',':
                        row.append(''.join(tok)); tok = []
                    elif ch == ')':
                        row.append(''.join(tok)); i += 1
                        break
                    else:
                        tok.append(ch)
                    i += 1
                yield row


MAINT = re.compile(r'^(Articles|All_|CS1|Pages_|Wikipedia|Webarchive|Use_|Short_description|Commons_|Coordinates|'
                   r'Official_website|Good_articles|Very_good_articles|Stub|.*_stubs$|Redirects|Hidden_|Living_people$|'
                   r'Templates|Infobox|Disambiguation|Harv_and_Sfn|Wikidata|Accuracy|Orphaned|Dead_|Lua|Track)')


def cmd_wiki(out, cl_path, page_path, lt_path):
    cat_title = {}      # linktarget id -> category title (namespace 14 only)
    for r in sql_rows(lt_path, 'linktarget'):
        if len(r) >= 3 and r[1] == '14':
            cat_title[int(r[0])] = r[2]
    page_ns = {}        # page_id -> (namespace, title)
    for r in sql_rows(page_path, 'page'):
        if len(r) >= 4 and r[1] in ('0', '14') and r[3] == '0':
            page_ns[int(r[0])] = (r[1], r[2])
    groups, gid = {}, {}
    def g(title):
        if title not in gid:
            gid[title] = len(gid); groups[gid[title]] = title
        return gid[title]
    nest, members, dropped_maint = set(), set(), 0
    for r in sql_rows(cl_path, 'categorylinks'):
        if len(r) < 7:
            continue
        frm, typ, tgt = int(r[0]), r[4], int(r[6])
        parent = cat_title.get(tgt)
        if parent is None or frm not in page_ns:
            continue
        if MAINT.match(parent):
            dropped_maint += 1
            continue
        ns, title = page_ns[frm]
        if typ == 'subcat' and ns == '14':
            if MAINT.match(title):
                continue
            nest.add((g(title), g(parent)))
        elif typ == 'page' and ns == '0':
            members.add((title, g(parent)))
    print('dropped maintenance-category links', dropped_maint)
    write(out, groups, sorted(nest), sorted(members))


# Fast regex variant for large dumps (same output as cmd_wiki)
Q = r"'((?:[^'\\]|\\.)*)'"
RE_LT = re.compile(r"\((\d+),14," + Q + r"\)")
RE_PG = re.compile(r"\((\d+),(0|14)," + Q + r",0,")
RE_CL = re.compile(r"\((\d+)," + Q + r",'[^']*'," + Q + r",'(page|subcat)',\d+,(\d+)\)")


def unesc(t):
    return re.sub(r"\\(.)", r"\1", t)


def fast_rows(path, rx):
    with gzip.open(path, 'rb') as f:
        for raw in f:
            if raw[:1] in (b'(', b'I'):
                for m in rx.finditer(raw.decode('utf-8', errors='replace')):
                    yield m.groups()


def cmd_wikifast(out, cl_path, page_path, lt_path):
    cat_title = {int(i): unesc(t) for i, t in fast_rows(lt_path, RE_LT)}
    print('linktarget categories', len(cat_title), flush=True)
    page_ns = {int(i): (ns, unesc(t)) for i, ns, t in fast_rows(page_path, RE_PG)}
    print('pages', len(page_ns), flush=True)
    groups, gid = {}, {}
    def g(title):
        if title not in gid:
            gid[title] = len(gid); groups[gid[title]] = title
        return gid[title]
    os.makedirs(out, exist_ok=True)
    fn, fm, dropped, nn, nm = open(f'{out}/nest.tsv', 'w'), open(f'{out}/members.tsv', 'w'), 0, 0, 0
    for frm, _, _, typ, tgt in fast_rows(cl_path, RE_CL):          # (cl_from, cl_target_id) is unique
        parent = cat_title.get(int(tgt)); pg = page_ns.get(int(frm))
        if parent is None or pg is None:
            continue
        if MAINT.match(parent):
            dropped += 1; continue
        ns, title = pg
        if typ == 'subcat' and ns == '14':
            if not MAINT.match(title):
                fn.write(f'{g(title)}\t{g(parent)}\n'); nn += 1
        elif typ == 'page' and ns == '0':
            fm.write(f'{title}\t{g(parent)}\n'); nm += 1
    fn.close(); fm.close()
    with open(f'{out}/groups.tsv', 'w') as f:
        for k, name in groups.items():
            f.write(f'{k}\t{name}\n')
    print('dropped maintenance-category links', dropped)
    print(out, 'groups', len(groups), 'nest', nn, 'members', nm)


if __name__ == '__main__':
    if sys.argv[1] == 'obo':
        cmd_obo(*sys.argv[2:6])
    elif sys.argv[1] == 'wiki':
        cmd_wiki(*sys.argv[2:6])
    elif sys.argv[1] == 'wikifast':
        cmd_wikifast(*sys.argv[2:6])
