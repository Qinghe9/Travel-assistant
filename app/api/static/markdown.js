(function (root) {
  'use strict';

  const INLINE_RE = /(\*\*[^*]+\*\*|\[\d+\])/g;

  function parseInlineTokens(text) {
    const tokens = [];
    let last = 0;
    const source = String(text || '');
    source.replace(INLINE_RE, (part, _match, offset) => {
      if (offset > last) {
        tokens.push({ type: 'text', text: source.slice(last, offset) });
      }
      if (part.startsWith('**')) {
        tokens.push({ type: 'strong', text: part.slice(2, -2) });
      } else {
        tokens.push({ type: 'cite', index: Number(part.slice(1, -1)) });
      }
      last = offset + part.length;
      return part;
    });
    if (last < source.length) {
      tokens.push({ type: 'text', text: source.slice(last) });
    }
    return tokens;
  }

  function parseMarkdownBlocks(text) {
    const blocks = [];
    let list = null;
    let quote = null;

    function flushList() {
      if (list) blocks.push(list);
      list = null;
    }

    function flushQuote() {
      if (quote) blocks.push(quote);
      quote = null;
    }

    for (const raw of String(text || '').split(/\r?\n/)) {
      const line = raw.trimEnd();
      if (!line.trim()) {
        flushList();
        flushQuote();
        continue;
      }

      const heading = line.match(/^(#{1,2})\s+(.+)$/);
      if (heading) {
        flushList();
        flushQuote();
        blocks.push({
          type: 'heading',
          level: heading[1].length,
          text: heading[2].trim()
        });
        continue;
      }

      const quoted = line.match(/^>\s?(.*)$/);
      if (quoted) {
        flushList();
        if (!quote) quote = { type: 'blockquote', lines: [] };
        quote.lines.push(quoted[1]);
        continue;
      }

      const item = line.match(/^\s*[-*]\s+(.+)$/);
      if (item) {
        flushQuote();
        if (!list) list = { type: 'list', items: [] };
        list.items.push(item[1]);
        continue;
      }

      flushList();
      flushQuote();
      blocks.push({ type: 'paragraph', text: line.trim() });
    }
    flushList();
    flushQuote();
    return blocks;
  }

  function appendInline(container, text, doc) {
    for (const token of parseInlineTokens(text)) {
      if (token.type === 'strong') {
        const strong = doc.createElement('strong');
        strong.textContent = token.text;
        container.appendChild(strong);
      } else if (token.type === 'cite') {
        const sup = doc.createElement('sup');
        sup.className = 'cite';
        const anchor = doc.createElement('a');
        anchor.href = '#cite-' + token.index;
        anchor.textContent = String(token.index);
        anchor.title = '查看来源 ' + token.index;
        if (anchor.addEventListener) {
          anchor.addEventListener('click', () => {
            const card = container.closest ? container.closest('.acard') : null;
            const sources = card ? card.querySelector('.sources') : null;
            if (sources) sources.open = true;
          });
        }
        sup.appendChild(anchor);
        container.appendChild(sup);
      } else if (token.text) {
        container.appendChild(doc.createTextNode(token.text));
      }
    }
  }

  function renderMarkdown(container, text, doc) {
    doc = doc || root.document;
    if (!container || !doc) return;
    if (typeof container.replaceChildren === 'function') {
      container.replaceChildren();
    } else {
      container.textContent = '';
    }

    for (const block of parseMarkdownBlocks(text)) {
      if (block.type === 'heading') {
        const heading = doc.createElement(block.level === 1 ? 'h1' : 'h2');
        heading.className = 'md-heading md-h' + block.level;
        appendInline(heading, block.text, doc);
        container.appendChild(heading);
      } else if (block.type === 'blockquote') {
        const quote = doc.createElement('blockquote');
        quote.className = 'md-quote';
        block.lines.forEach((line, index) => {
          if (index) quote.appendChild(doc.createElement('br'));
          appendInline(quote, line, doc);
        });
        container.appendChild(quote);
      } else if (block.type === 'list') {
        const list = doc.createElement('ul');
        list.className = 'md-list';
        for (const item of block.items) {
          const li = doc.createElement('li');
          appendInline(li, item, doc);
          list.appendChild(li);
        }
        container.appendChild(list);
      } else {
        const paragraph = doc.createElement('p');
        paragraph.className = 'md-paragraph';
        appendInline(paragraph, block.text, doc);
        container.appendChild(paragraph);
      }
    }
  }

  function completeMarkdownPrefix(text) {
    const source = String(text || '');
    const index = source.lastIndexOf('\n');
    return index >= 0 ? source.slice(0, index + 1) : '';
  }

  const api = { parseInlineTokens, parseMarkdownBlocks, renderMarkdown, completeMarkdownPrefix };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.TravelMarkdown = api;
})(typeof globalThis !== 'undefined' ? globalThis : window);
