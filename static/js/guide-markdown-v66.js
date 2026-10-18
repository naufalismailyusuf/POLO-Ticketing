/* Guide Markdown (Revisi 66). Safe DOM renderer, never inserts user HTML.
   Supports headings, lists, tables, code fences, blockquotes, links and emphasis. */
(() => {
  'use strict';
  const el = (tag, cls) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    return e;
  };
  const txt = (str) => document.createTextNode(str);
  const safeUrl = (input) => {
    if (!/^https?:\/\//i.test(input)) return null;
    try {
      const url = new URL(input);
      return /^https?:$/.test(url.protocol) ? url.href : null;
    } catch { return null; }
  };
  function inline(text, target) {
    // A conservative inline renderer: all unmatched Markdown and HTML are literal text.
    const pattern = /(`[^`\n]+`|\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)|\*\*[^*\n]+\*\*|\*[^*\n]+\*|~~[^~\n]+~~)/g;
    let pos = 0, match;
    while ((match = pattern.exec(text))) {
      if (match.index > pos) target.append(txt(text.slice(pos, match.index)));
      const token = match[0];
      if (token.startsWith('`')) {
        const code = el('code'); code.textContent = token.slice(1, -1); target.append(code);
      } else if (token.startsWith('[')) {
        const href = safeUrl(match[3]);
        if (href) {
          const a = el('a'); a.href = href; a.rel = 'noopener noreferrer';
          a.target = '_blank'; a.textContent = match[2]; target.append(a);
        } else target.append(txt(token));
      } else {
        const isStrong = token.startsWith('**');
        const isDel = token.startsWith('~~');
        const tag = isStrong ? 'strong' : isDel ? 'del' : 'em';
        const offset = isStrong || isDel ? 2 : 1;
        const n = el(tag); n.textContent = token.slice(offset, -offset); target.append(n);
      }
      pos = pattern.lastIndex;
    }
    if (pos < text.length) target.append(txt(text.slice(pos)));
  }
  const parts = line => line.trim().replace(/^\||\|$/g, '').split(/\s*\|\s*/);
  function render(container, markdown) {
    container.replaceChildren();
    const lines = String(markdown || '').replace(/\r\n?/g, '\n').split('\n');
    let i = 0;
    const isFence = line => /^\s{0,3}(`{3,}|~{3,})/.exec(line);
    const heading = line => /^\s{0,3}(#{1,6})\s+(.+)$/.exec(line);
    const bullet = line => /^\s{0,3}[-*+]\s+(.+)$/.exec(line);
    const ordered = line => /^\s{0,3}\d+[.)]\s+(.+)$/.exec(line);
    const quote = line => /^\s{0,3}>\s?(.*)$/.exec(line);
    const isTable = index => index + 1 < lines.length && lines[index].includes('|') && /^\s*\|?\s*:?-{3,}:?(\s*\|\s*:?-{3,}:?)*\s*\|?\s*$/.test(lines[index+1]);
    function special(index) {
      const l = lines[index] || '';
      return !l.trim() || isFence(l) || heading(l) || bullet(l) || ordered(l) || quote(l) || isTable(index) || /^\s{0,3}(-{3,}|\*{3,}|_{3,})\s*$/.test(l);
    }
    while (i < lines.length) {
      const line = lines[i];
      if (!line.trim()) { i++; continue; }
      const fence = isFence(line);
      if (fence) {
        const marker = fence[1][0], min = fence[1].length;
        const language = line.slice(fence[0].length).trim().replace(/[^a-z0-9+_.-]/gi, '').slice(0,32);
        const rows = [];i++;
        while (i < lines.length && !(new RegExp('^\\s{0,3}' + (marker === '`' ? '`' : '~') + '{' + min + ',}\\s*$')).test(lines[i])) rows.push(lines[i++]);
        if (i < lines.length) i++;
        const box = el('div', 'gm-code');
        const bar = el('div', 'gm-code-toolbar');
        const caption = el('span');caption.textContent = language || 'Command';
        const copy = el('button'); copy.type = 'button';copy.textContent = 'Copy';
        copy.addEventListener('click', async () => {
          try { await navigator.clipboard.writeText(rows.join('\n'));copy.textContent = 'Copied';
            setTimeout(() => {copy.textContent = 'Copy';}, 1500);
          } catch {copy.textContent = 'Select to copy';}
        });
        bar.append(caption, copy);
        const pre = el('pre');const code = el('code');code.textContent = rows.join('\n');pre.append(code);
        box.append(bar, pre);container.append(box);continue;
      }
      const matchHeading = heading(line);
      if (matchHeading) {
        const h = el('h' + matchHeading[1].length);
        inline(matchHeading[2], h);container.append(h);i++;continue;
      }
      if (/^\s{0,3}(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {container.append(el('hr'));i++;continue;}
      if (isTable(i)) {
        const table = el('table'); const head = el('thead');const tr = el('tr');
        for (const val of parts(line)) {const th=el('th');inline(val,th);tr.append(th);}
        head.append(tr);table.append(head);i+=2;
        const body=el('tbody');
        while (i<lines.length && lines[i].trim() && lines[i].includes('|')) {
          const tr=el('tr');for(const val of parts(lines[i])) {const td=el('td');inline(val,td);tr.append(td);} body.append(tr);i++;
        }
        table.append(body);const holder=el('div','gm-table-wrap');holder.append(table);container.append(holder);continue;
      }
      const listMatch = bullet(line) || ordered(line);
      if (listMatch) {
        const orderedList=!!ordered(line);const list=el(orderedList?'ol':'ul');
        while (i<lines.length) {
          const m=orderedList?ordered(lines[i]):bullet(lines[i]);
          if (!m) break;
          const li=el('li');let item=m[1];
          const check=/^\[([ xX])\]\s+/.exec(item);
          if(check){const mark=el('span','gm-check');mark.textContent=check[1].toLowerCase()==='x'?'☑':'☐';li.append(mark);item=item.slice(check[0].length);}
          inline(item,li);list.append(li);i++;
        }
        container.append(list);continue;
      }
      const q=quote(line);
      if (q) {
        const b=el('blockquote');const text=[];
        while(i<lines.length && quote(lines[i])) text.push(quote(lines[i++])[1]);
        inline(text.join(' '),b);container.append(b);continue;
      }
      const chunks=[line];i++;
      while(i<lines.length&&!special(i)) chunks.push(lines[i++]);
      const p=el('p');inline(chunks.join(' '),p);container.append(p);
    }
  }
  window.PoloGuideMarkdown = Object.freeze({render});
})();
