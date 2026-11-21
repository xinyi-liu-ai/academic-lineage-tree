"""发布前运行：给 docs/index.html 里的 style.css / app.js 换一个新版本号（也用于 graph.json），
让浏览器不再用旧缓存。用法: python scripts/bump_version.py"""
import os, re, time
p = os.path.join(os.path.dirname(__file__), '..', 'docs', 'index.html')
s = open(p, encoding='utf-8').read()
v = time.strftime('%Y%m%d%H%M')
# graph.json 出现在 <link rel="preload">，app.js 直接用那个地址去 fetch，三处版本号保持一致
s2 = re.sub(r'(style\.css|app\.js|graph\.json)\?v=[\w-]+', lambda m: f'{m.group(1)}?v={v}', s)
open(p, 'w', encoding='utf-8').write(s2)
print('version', v)
