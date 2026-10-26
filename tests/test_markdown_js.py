import shutil
import subprocess

import pytest


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js 不可用")
def test_markdown_subset_renders_heading_quote_strong_and_citation():
    script = r"""
const md = require('./app/api/static/markdown.js');
const blocks = md.parseMarkdownBlocks('## 标题\n> 提示\n- **上午**：景点 [1]');
if (blocks[0].type !== 'heading' || blocks[0].level !== 2) throw new Error('heading');
if (blocks[1].type !== 'blockquote') throw new Error('quote');
if (blocks[2].type !== 'list') throw new Error('list');

class El {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.className = ''; }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren() { this.children = []; }
  addEventListener() {}
}
const doc = {
  createElement: (tag) => new El(tag),
  createTextNode: (text) => ({ nodeType: 3, textContent: text }),
};
const root = new El('div');
md.renderMarkdown(root, '## 标题\n> 提示\n- **上午**：景点 [1]', doc);
if (root.children[0].tagName !== 'H2') throw new Error('H2');
if (root.children[1].tagName !== 'BLOCKQUOTE') throw new Error('BLOCKQUOTE');
if (root.children[2].children[0].children[0].tagName !== 'STRONG') throw new Error('STRONG');
if (md.completeMarkdownPrefix('## 标题\n半行') !== '## 标题\n') throw new Error('buffer');
"""
    subprocess.run(["node", "-e", script], check=True)
