"""姓名清洗与匹配工具。"""
import re
import unicodedata

TITLE_RE = re.compile(r'^((?:distinguished|assistant|associate)\s+professors?|profs?\.?|professors?|drs?\.?|mr\.?|ms\.?)\s+', re.I)

# 表里 PDF 复制出来的乱码，人工对照修正
MOJIBAKE = {
    'Christopher R ??e': 'Christopher Ré', 'Christopher R??': 'Christopher Ré', 'Geo??€ Gordon': 'Geoff Gordon',
    'Antonio Pescap??': 'Antonio Pescapè', 'KC Cla??€y': 'KC Claffy', 'Krste Asanovi??': 'Krste Asanović',
    'Fran??ois Baccelli': 'François Baccelli', 'St??phane Mallat': 'Stéphane Mallat',
    'Emmanuel J. Cand??s': 'Emmanuel J. Candès', 'Ren??e Vidal': 'René Vidal', 'Peter Richt??rik': 'Peter Richtárik',
    'Ren?? ee J. Miller': 'Renée J. Miller', 'Nata??a Pr??ulj': 'Nataša Pržulj', 'Fr??do Durand': 'Frédo Durand',
    'Jos?? F. Mart??nez': 'José F. Martínez', 'David Mazi??res': 'David Mazières', 'Maja J Matari??': 'Maja J Matarić',
    'Grigore Ro??u': 'Grigore Roșu', 'Hanspeter P???ster': 'Hanspeter Pfister', 'Hanspeter P??ster': 'Hanspeter Pfister',
    'Bj??rn Hartmann': 'Björn Hartmann', 'Nenad Medvidovi??c': 'Nenad Medvidović', 'Zoran Popovi??': 'Zoran Popović',
    'Zubair Sha???q': 'Zubair Shafiq', 'St??phane Lafortune': 'Stéphane Lafortune', 'Annie I. Ant??n': 'Annie I. Antón',
    'Kimmen Sjolander': 'Kimmen Sjölander', 'Hal Daume III': 'Hal Daumé III',
}
NOTE_RE = re.compile(r'\((?:phd |postdoc )?advisor\)|\(phd advisor\)', re.I)
SPLIT_RE = re.compile(r'\s*(?:;|&|\band\b|\|)\s*', re.I)
DBLP_SUFFIX = re.compile(r'\s+\d{4}$')
JUNK_RE = re.compile(r'universit|institute|\bunit\b|\blab\b|group|missing|repeating|^n/?a$|^none$|^no\.?$|name\(s\)', re.I)


def fold(s):
    """去掉重音、转小写、只留字母和空格。"""
    s = unicodedata.normalize('NFKD', s)
    s = ''.join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r'[^a-zA-Z?\s-]', ' ', s).replace('-', ' ')
    return re.sub(r'\s+', ' ', s).strip().lower()


def display_name(name):
    """CSRankings 名字去掉 dblp 消歧后缀，如 'Abhinav Gupta 0001'。"""
    return DBLP_SUFFIX.sub('', name.strip())


def key(name):
    """匹配键：名 + 姓（忽略中间名、首字母）。"""
    toks = [t for t in fold(display_name(name)).split() if len(t.strip('?')) > 1 or '?' in t]
    toks = [t for t in toks if t not in {'jr', 'sr', 'ii', 'iii', 'iv'}]
    if not toks:
        return ''
    return toks[0] + ' ' + toks[-1] if len(toks) > 1 else toks[0]


def split_advisors(raw):
    """把一个导师单元格拆成若干清洗后的姓名。"""
    if not raw:
        return []
    raw = raw.replace('_x007f_', '').replace('_x0013_', '')
    raw = NOTE_RE.sub('', raw)
    # 极少数用逗号分隔两个完整姓名
    parts = []
    for p in SPLIT_RE.split(raw):
        parts += [q for q in re.split(r',\s+(?=[A-Z][a-z]+\s+[A-Z])', p)]
    out = []
    for p in parts:
        p = p.strip().strip('.,').strip()
        p = TITLE_RE.sub('', p)
        p = TITLE_RE.sub('', p).strip().strip('.,').strip()
        p = MOJIBAKE.get(p, p)
        if not p or JUNK_RE.search(p) or len(p) < 4 or ' ' not in p:
            continue
        out.append(p)
    return out


def wildcard_regex(name):
    """乱码 '??' 当通配符，用于在已知姓名里找回原名。"""
    f = fold(name)
    pat = re.escape(f).replace(r'\?', '?')
    pat = re.sub(r'\?+', '.{0,3}', pat)
    return re.compile('^' + pat + '$')
